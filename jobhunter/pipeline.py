"""Orquestração da execução diária.

    1. buscar      -> termos expandidos × locais × fontes  -> tabela jobs (deduplicada)
    2. pré-filtro  -> regras baratas, sem LLM (palavras proibidas no título, sem descrição...)
    3. triagem     -> 1 chamada por vaga nova (os 2 candidatos juntos)
    4. avaliação   -> 1 chamada por vaga × candidato relevante -> tabela matches
    5. adaptação   -> CV + carta + auditoria para as N melhores 'apply' -> tabela applications
    6. envio       -> conforme apply.mode (fila / e-mail)
    7. relatório   -> data/reports/AAAA-MM-DD.html e .csv
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import matcher, report
from .apply import send_email
from .config import ROOT, Candidate, load_candidates
from .db import DB, now
from .query_expansion import build_terms
from .render import render_doc, slug
from .sources import build_sources
from .tailor import tailor, verify

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: dict, db: DB | None = None, sources=None):
        self.s = settings
        self.data = ROOT / settings.get("data_dir", "data")
        self.db = db or DB(self.data / "jobhunter.db")
        self.candidates: list[Candidate] = load_candidates(settings)
        self.sources = sources if sources is not None else build_sources(settings)
        self.llm = settings["llm"]
        self.stats: dict = {}

    # ---------- 1. busca ----------
    def search(self) -> list[int]:
        terms: list[str] = []
        for c in self.candidates:
            terms += build_terms(c, self.llm["expand"], self.data, self.s["search"].get("max_terms_per_candidate", 15))
        terms = list(dict.fromkeys(terms))
        log.info("%d termos de busca: %s", len(terms), terms)

        new_ids: list[int] = []
        for loc in self.s["search"]["locations"]:
            for term in terms:
                for src in self.sources:
                    for job in src.search(term, loc["name"], loc["country"]):
                        if not job.title or not job.url:
                            continue
                        job_id, is_new = self.db.upsert_job(job)
                        if is_new:
                            new_ids.append(job_id)
        self.stats["new_jobs"] = len(new_ids)
        log.info("%d vagas novas", len(new_ids))
        return new_ids

    # ---------- 2. pré-filtro ----------
    def prefilter(self, job_row) -> str | None:
        pf = self.s.get("prefilter", {})
        title = (job_row["title"] or "").lower()
        for pat in pf.get("exclude_title_patterns", []):
            if re.search(pat, title, re.I):
                return f"título contém '{pat}'"
        if len(job_row["description"] or "") < pf.get("min_description_chars", 200):
            return "descrição ausente/curta demais para avaliar"
        return None

    # ---------- 3+4. triagem e avaliação ----------
    def match(self, job_ids: list[int]):
        th = self.s["thresholds"]
        weights = self.s.get("weights")
        counters = {"prefiltered": 0, "triaged": 0, "assessed": 0, "apply": 0, "review": 0}
        lim = self.s.get("limits", {})
        max_triage, max_assess = lim.get("max_triage_per_day", 10**9), lim.get("max_assess_per_day", 10**9)
        for jid in job_ids:
            if counters["triaged"] >= max_triage or counters["assessed"] >= max_assess:
                log.warning("teto diário de chamadas atingido (%s); o resto fica para amanhã", lim)
                break  # vagas não avaliadas continuam sem match e são retomadas na próxima execução
            row = self.db.job(jid)
            todo = [c for c in self.candidates if not self.db.has_match(jid, c.id)]
            if not todo:
                continue
            why = self.prefilter(row)
            if why:
                counters["prefiltered"] += 1
                for c in todo:
                    self.db.save_match(jid, c.id, triage_relevant=0, triage_reason=why, decision="skip")
                continue
            try:
                t = matcher.triage(row, todo, self.llm["triage"])
            except Exception as e:
                log.warning("triagem falhou para job %s: %s", jid, e)
                continue
            counters["triaged"] += 1
            self.db.set_triage(jid, t.job_language, t.seniority, t.hard_requirements)
            row = self.db.job(jid)
            verdict = {ct.candidate: ct for ct in t.candidates}
            for c in todo:
                ct = verdict.get(c.id)
                if not ct or not ct.relevant:
                    self.db.save_match(jid, c.id, triage_relevant=0,
                                       triage_reason=ct.reason if ct else "não avaliado", decision="skip")
                    continue
                try:
                    a = matcher.assess(row, c, self.llm["assess"])
                except Exception as e:
                    log.warning("avaliação falhou job %s/%s: %s", jid, c.id, e)
                    continue
                counters["assessed"] += 1
                cur, pot = matcher.score(a, weights)
                decision, prio = matcher.decide(cur, pot, a.desire_fit, a.knockouts, th)
                counters[decision] = counters.get(decision, 0) + 1
                self.db.save_match(jid, c.id, triage_relevant=1, triage_reason=ct.reason,
                                   score_current=cur, score_potential=pot, desire_fit=a.desire_fit,
                                   priority=prio, decision=decision, assessment_json=a.model_dump_json(),
                                   model=self.llm["assess"]["model"])
        self.stats.update(counters)

    # ---------- 5+6. adaptação e envio ----------
    def tailor_and_apply(self):
        mode = self.s["apply"].get("mode", "queue")
        limit = self.s["apply"].get("max_per_candidate_per_day", 5)
        made = sent = 0
        from .models import MatchAssessment
        for c in self.candidates:
            for m in self.db.matches_to_tailor(c.id, limit):
                row = self.db.job(m["job_id"])
                a = MatchAssessment.model_validate_json(m["assessment_json"])
                try:
                    docs = tailor(row, c, a, self.llm["tailor"])
                    v = verify(docs, c, self.llm["verify"])
                except Exception as e:
                    log.warning("adaptação falhou job %s/%s: %s", row["id"], c.id, e)
                    continue
                out = self.data / "applications" / c.id / f"{row['id']}-{slug(row['company'])}-{slug(row['title'])}"
                base = slug(c.name)
                cv_path = render_doc(docs.cv_markdown, out, f"CV-{base}")
                letter_path = render_doc(docs.cover_letter_markdown, out, f"Lettre-{base}")
                (out / "audit.json").write_text(json.dumps(
                    {"changes_made": docs.changes_made, "facts_used": docs.facts_used,
                     "unsupported_claims": v.unsupported_claims}, ensure_ascii=False, indent=2), encoding="utf-8")
                status = "drafted" if v.ok else "needs_review"
                method = "email" if row["apply_email"] else "portal"
                notes = None if v.ok else "Auditor encontrou: " + "; ".join(v.unsupported_claims)

                if mode == "email" and method == "email" and v.ok:
                    ok = send_email(
                        cfg=self.s["apply"]["smtp"], sender_name=c.name, to=row["apply_email"],
                        subject=f"Candidature – {row['title']}", body=docs.cover_letter_markdown,
                        attachments=[p for p in (cv_path, letter_path) if p.suffix == ".pdf"],
                        dry_run=self.s["apply"].get("dry_run", True))
                    if ok:
                        status, sent = "sent", sent + 1

                self.db.save_application(row["id"], c.id, status=status, method=method,
                                         cv_markdown=docs.cv_markdown,
                                         cover_letter_markdown=docs.cover_letter_markdown,
                                         cv_path=str(cv_path), letter_path=str(letter_path),
                                         changes_made=json.dumps(docs.changes_made, ensure_ascii=False),
                                         facts_used=json.dumps(docs.facts_used, ensure_ascii=False),
                                         notes=notes)
                made += 1
        self.stats.update(tailored=made, sent=sent)

    # ---------- tudo ----------
    def run(self, skip_search: bool = False) -> Path:
        started = now()
        if not skip_search:
            self.search()
        # vagas novas + as que ficaram pendentes por teto de custo em dias anteriores (mais novas primeiro)
        ids = [r["id"] for r in self.db.conn.execute(
            "SELECT j.id FROM jobs j WHERE (SELECT COUNT(*) FROM matches m WHERE m.job_id=j.id) < ?"
            " ORDER BY j.id DESC", (len(self.candidates),))]
        self.match(ids)
        self.tailor_and_apply()
        path = report.build(self.db, self.data / "reports", since=started[:10])
        self.db.log_run(started, self.stats)
        log.info("concluído: %s — relatório: %s", self.stats, path)
        return path
