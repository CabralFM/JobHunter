"""Teste ponta a ponta SEM rede e SEM gastar API: fonte falsa + LLM falso."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from jobhunter import config, llm, matcher, pipeline, query_expansion, tailor
from jobhunter.db import DB
from jobhunter.models import (CandidateTriage, DimensionScore, Job, MatchAssessment, TailoredDocs, Triage)
from jobhunter.sources.base import Source

DESC = "Nous recherchons un Chef de projet IT pour piloter la transformation digitale. " * 10


class FakeSource(Source):
    name = "fake"

    def search(self, query, location, country):
        return [
            Job("linkedin", "1", "https://x/1", "Chef de projet IT (h/f) 80-100%", "Banque Alpha", location, country, DESC,
                apply_email="rh@alpha.ch"),
            # mesma vaga em outro site -> deve deduplicar
            Job("jobup.ch", "9", "https://y/9", "Chef de projet IT", "Banque Alpha", location, country, DESC + " extra"),
            Job("indeed", "2", "https://x/2", "Stagiaire marketing", "Beta SA", location, country, DESC),
            Job("indeed", "3", "https://x/3", "Infirmier", "Hôpital", location, country, DESC),
        ]


def fake_ask(*, system, user, schema, step_cfg, max_tokens=0):
    if schema is query_expansion.SearchPlan:
        return schema(terms=[query_expansion.SearchTerm(term="chef de projet IT", language="fr", rationale="alvo")])
    if schema is Triage:
        rel = "Infirmier" not in user
        return Triage(job_language="fr", seniority="senior", hard_requirements=[],
                      candidates=[CandidateTriage(candidate="eu", relevant=rel, reason="r")])
    if schema is MatchAssessment:
        dims = [DimensionScore(name=n, score_current_cv=50, score_potential=85, evidence="e")
                for n in matcher.DEFAULT_WEIGHTS]
        return MatchAssessment(dimensions=dims, desire_fit=80, knockouts=[], gaps_fixable_by_tailoring=["PMO"],
                               real_gaps=[], keywords_to_mirror=["transformation digitale"], summary="bom")
    if schema is TailoredDocs:
        return TailoredDocs(cv_markdown="# Nome\n## Expérience\n- x", cover_letter_markdown="Madame, Monsieur,",
                            changes_made=["resumo"], facts_used=["x"])
    if schema is tailor.Verification:
        return tailor.Verification(unsupported_claims=[], ok=True)
    raise AssertionError(schema)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path
    cand = root / "config" / "candidates" / "eu"
    shutil.copytree(Path(config.CONFIG_DIR) / "candidates" / "_exemplo", cand)
    settings = yaml.safe_load((Path(config.CONFIG_DIR) / "settings.example.yaml").read_text(encoding="utf-8"))
    settings["candidates"] = ["eu"]
    settings["search"]["locations"] = [{"name": "Lausanne", "country": "CH"}]
    monkeypatch.setattr(config, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(pipeline, "ROOT", root)
    for mod in (query_expansion, matcher, tailor):
        monkeypatch.setattr(mod, "ask", fake_ask)
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr("jobhunter.render.html_to_pdf", lambda html, out: False)
    return root, settings


def test_full_run(env):
    root, settings = env
    p = pipeline.Pipeline(settings, sources=[FakeSource()])
    report = p.run()
    db: DB = p.db

    assert db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3  # 4 anúncios, 1 duplicado
    dup = db.conn.execute("SELECT also_seen_on, description FROM jobs WHERE company='Banque Alpha'").fetchone()
    assert "jobup.ch" in dup["also_seen_on"] and dup["description"].endswith("extra")

    decisions = dict(db.conn.execute("SELECT j.title, m.decision FROM matches m JOIN jobs j ON j.id=m.job_id"))
    assert decisions["Stagiaire marketing"] == "skip"      # pré-filtro por regex
    assert decisions["Infirmier"] == "skip"                # triagem
    assert decisions["Chef de projet IT (h/f) 80-100%"] == "apply"

    app = db.conn.execute("SELECT * FROM applications").fetchone()
    assert app["status"] == "drafted" and app["method"] == "email"
    assert Path(app["cv_path"]).exists()
    assert report.exists() and "Banque Alpha" in report.read_text(encoding="utf-8")

    # segunda execução não reprocessa nem duplica
    p.run()
    assert db.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1


def test_score_and_decide():
    dims = [DimensionScore(name=n, score_current_cv=40, score_potential=80, evidence="")
            for n in matcher.DEFAULT_WEIGHTS]
    a = MatchAssessment(dimensions=dims, desire_fit=50, knockouts=[], gaps_fixable_by_tailoring=[],
                        real_gaps=[], keywords_to_mirror=[], summary="")
    assert matcher.score(a) == (40.0, 80.0)
    th = {"apply_min_potential": 72, "apply_min_desire": 60, "review_min_potential": 58}
    assert matcher.decide(40, 80, 50, [], th)[0] == "review"    # sabe fazer, mas não quer tanto
    assert matcher.decide(40, 80, 70, [], th)[0] == "apply"
    assert matcher.decide(40, 80, 70, ["allemand C1"], th)[0] == "knockout"


def test_documents_language_policy():
    from jobhunter.config import Candidate
    c = Candidate("eu", {"documents_language": {"CH": "fr", "LU": "job"}}, "", "")
    assert tailor.documents_language({"language": "en", "country": "CH"}, c) == "fr"
    assert tailor.documents_language({"language": "en", "country": "LU"}, c) == "en"
    assert tailor.documents_language({"language": "de", "country": "CH"},
                                     Candidate("x", {}, "", "")) == "de"


def test_load_dotenv(tmp_path, monkeypatch):
    from jobhunter.__main__ import load_dotenv
    (tmp_path / ".env").write_text("# c\nJH_TEST_A=1\nJH_TEST_B=\"x y\"\nJH_TEST_EMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("JH_TEST_A", raising=False)
    monkeypatch.setenv("JH_TEST_B", "keep")
    load_dotenv(tmp_path / ".env")
    import os
    assert os.environ["JH_TEST_A"] == "1" and os.environ["JH_TEST_B"] == "keep"
    assert "JH_TEST_EMPTY" not in os.environ


def test_search_terms_cache_utf8_and_corrupt(tmp_path, monkeypatch):
    """No Windows a codificação padrão é cp1252; termos com '→' não podem quebrar a gravação,
    e um cache corrompido por gravação interrompida deve ser regenerado."""
    import locale
    from jobhunter.config import Candidate
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "cp1252")
    monkeypatch.setattr(query_expansion, "ask", lambda **k: query_expansion.SearchPlan(terms=[
        query_expansion.SearchTerm(term="SAP MM", language="en", rationale="MM → S/4HANA")]))
    c = Candidate("x", {}, "cv", "cv")
    (tmp_path / "search_terms_x.json").write_text('{"fingerprint": "abc", "ter', encoding="utf-8")
    assert query_expansion.build_terms(c, {}, tmp_path) == ["SAP MM"]
    assert "→" in (tmp_path / "search_terms_x.json").read_text(encoding="utf-8")


def test_search_call_plan(env):
    """Fontes nacionais: 1 busca por termo e país. Cidades com max_terms usam só os primeiros termos."""
    root, settings = env
    calls = []

    class Spy(Source):
        def __init__(self, name, national):
            self.name, self.national = name, national

        def search(self, query, location, country):
            calls.append((self.name, query, location, country))
            return []

    settings["search"]["locations"] = [
        {"name": "Genève", "country": "CH"},
        {"name": "Zürich", "country": "CH", "max_terms": 1},
        {"name": "Luxembourg", "country": "LU"},
    ]
    p = pipeline.Pipeline(settings, sources=[Spy("local", False), Spy("jobup.ch", True)])
    import jobhunter.pipeline as pl
    orig = pl.build_terms
    pl.build_terms = lambda c, cfg, d, n=15: ["t1", "t2", "t3"]
    try:
        p.search()
    finally:
        pl.build_terms = orig
    local = [c for c in calls if c[0] == "local"]
    national = [c for c in calls if c[0] == "jobup.ch"]
    assert len(local) == 3 + 1 + 3                     # Genève 3 termos, Zürich 1, Luxemburgo 3
    assert {(c[1], c[3]) for c in national} == {(t, cc) for t in ("t1", "t2", "t3") for cc in ("CH", "LU")}
    assert all(c[2] == "" for c in national)           # sem cidade


def test_jobspy_country_search_passes_no_location(monkeypatch):
    """Busca por país: a JobSpy recebe location=None (país inteiro no domínio ch./lu.indeed.com)."""
    import pandas as pd
    import jobspy
    from jobhunter.sources.jobspy_source import JobSpySource
    seen = {}

    def fake_scrape(**kw):
        seen.update(kw)
        return pd.DataFrame([{"site": "indeed", "id": "1", "job_url": "u", "title": "Data Analyst",
                              "company": "X", "location": None, "description": "d"}])

    monkeypatch.setattr(jobspy, "scrape_jobs", fake_scrape)
    jobs = JobSpySource({"sites": ["indeed"]}).search("data analyst", "", "LU")
    assert seen["location"] is None and seen["country_indeed"] == "luxembourg"
    assert jobs[0].location == "LU"


def test_max_age_filter():
    from datetime import date
    today = date(2026, 11, 25)
    assert pipeline.too_old("2026-11-17", 7, today) is True
    assert pipeline.too_old("2026-11-18", 7, today) is False
    assert pipeline.too_old("2026-11-20T09:15:00+01:00", 7, today) is False
    assert pipeline.too_old("2026-11-01T00:00:00Z", 7, today) is True
    assert pipeline.too_old(None, 7, today) is False           # data desconhecida: mantém
    assert pipeline.too_old("há 3 dias", 7, today) is False     # ilegível: mantém
    assert pipeline.too_old("2020-01-01", None, today) is False  # sem filtro


def test_max_age_sets_source_windows():
    from jobhunter.sources import build_sources
    s = {"search": {"max_age_days": 7},
         "sources": {"jobspy": {"enabled": True, "sites": ["indeed"]}, "linkedin": {"enabled": True}}}
    srcs = {x.name: x for x in build_sources(s)}
    assert srcs["jobspy"].hours_old == 168 and srcs["linkedin"].hours_old == 168


def test_prioritize_romandie_and_title_first(env):
    import json as _json
    root, settings = env
    p = pipeline.Pipeline(settings, sources=[])
    p.data.mkdir(parents=True, exist_ok=True)
    (p.data / "search_terms_eu.json").write_text(_json.dumps({"terms": ["Data Analyst"]}), encoding="utf-8")
    rows = [
        {"id": 1, "title": "Data Analyst", "location": "Luxembourg", "country": "LU", "posted_at": "2026-11-25"},
        {"id": 2, "title": "Infirmier", "location": "Lausanne, Vaud", "country": "CH", "posted_at": "2026-11-25"},
        {"id": 3, "title": "Senior Data Analyst", "location": "Genève, Suisse", "country": "CH", "posted_at": "2026-11-20"},
        {"id": 4, "title": "Data Analyst", "location": "Zürich", "country": "CH", "posted_at": "2026-11-24"},
    ]
    assert p.prioritize(rows) == [3, 4, 1, 2]


def test_dedupe_merges_same_job_with_different_company_spelling(tmp_path):
    from jobhunter.db import DB
    from jobhunter.models import Job
    from jobhunter.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    # simula vagas gravadas com a chave antiga (empresa sem normalização)
    ids = []
    for src, comp in (("jobup.ch", "Banque Lombard Odier & Cie SA"), ("indeed", "Lombard Odier & Cie"),
                      ("linkedin", "Lombard Odier Group")):
        j = Job(src, src, "u-" + src, "Data Analyst", comp, "Genève", "CH", "d" * 300)
        cur = db.conn.execute("INSERT INTO jobs (dedup_key, source, external_id, url, title, company, country, description)"
                              " VALUES (?,?,?,?,?,?,?,?)", (src, src, src, j.url, j.title, comp, "CH", j.description))
        ids.append(cur.lastrowid)
    db.save_match(ids[1], "eu", decision="apply")
    db.save_application(ids[2], "eu", status="drafted")
    p = Pipeline.__new__(Pipeline)
    p.db = db
    assert p.dedupe() == 2
    rows = db.conn.execute("SELECT id, also_seen_on FROM jobs").fetchall()
    assert [r[0] for r in rows] == [ids[2]]                 # ficou a que tem candidatura
    assert "jobup.ch" in rows[0][1] and "indeed" in rows[0][1]
    # uma nova coleta da mesma vaga não cria linha nova
    _, is_new = db.upsert_job(Job("jobs.ch", "x", "u-x", "Data Analyst", "LOMBARD ODIER", "Genève", "CH", "d"))
    assert not is_new


def test_fatal_api_error_stops_run(env, monkeypatch):
    from jobhunter.llm import FatalAPIError
    root, settings = env
    calls = []

    def broke(**k):
        calls.append(1)
        raise FatalAPIError("sem crédito")
    monkeypatch.setattr(matcher, "ask", broke)
    p = pipeline.Pipeline(settings, sources=[FakeSource()])
    p.run()
    assert len(calls) == 1 and "aborted" in p.stats


def test_mobility_per_country():
    from types import SimpleNamespace
    from jobhunter.tailor import mobility_for
    cand = SimpleNamespace(profile={"mobility": {"CH": "Suisse romande", "LU": "Luxembourg"}})
    assert mobility_for({"country": "LU"}, cand) == "Luxembourg"
    assert mobility_for({"country": "CH"}, cand) == "Suisse romande"
    assert mobility_for({"country": "FR"}, cand) == "Suisse romande"   # sem chave: primeira
    cand2 = SimpleNamespace(profile={"mobility": "texto único"})
    assert mobility_for({"country": "LU"}, cand2) == "texto único"


def test_budget_guard(tmp_path):
    from types import SimpleNamespace
    import pytest
    from jobhunter import llm
    llm.set_budget(tmp_path, 0.001)
    u = SimpleNamespace(input_tokens=5000, output_tokens=1000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    cost = llm.call_cost("claude-haiku-5-5", u)
    assert abs(cost - 0.001) < 1e-9          # 5000*0.10/1M + 1000*0.50/1M
    llm._add_spend(cost)
    with pytest.raises(llm.FatalAPIError):
        llm._check_budget()
    llm.set_budget(tmp_path, None)


def test_usage_limit_is_fatal():
    from jobhunter import llm
    e = RuntimeError("Error code: 400 - You have reached your specified API usage limits. "
                     "You will regain access on 2026-12-01 at 00:00 UTC.")
    out = llm._fatal_or_same(e)
    assert isinstance(out, llm.FatalAPIError) and "Limits" in str(out)
    out = llm._fatal_or_same(RuntimeError("Your credit balance is too low"))
    assert isinstance(out, llm.FatalAPIError) and "Billing" in str(out)
    other = RuntimeError("bad schema")
    assert llm._fatal_or_same(other) is other
