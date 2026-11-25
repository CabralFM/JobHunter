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
    settings = yaml.safe_load((Path(config.CONFIG_DIR) / "settings.example.yaml").read_text())
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
    assert report.exists() and "Banque Alpha" in report.read_text()

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
