"""Estruturas de dados do pipeline.

`Job` é o formato normalizado que TODAS as fontes devolvem (LinkedIn, jobs.ch, Apify...).
Os modelos Pydantic `Triage`, `MatchAssessment` e `TailoredDocs` são os formatos de saída
estruturada pedidos ao Claude (o SDK valida o JSON contra eles).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


COMPANY_NOISE = {"sa", "ag", "ltd", "limited", "gmbh", "sarl", "sas", "llc", "inc", "plc", "nv", "bv", "spa",
                 "co", "cie", "company", "group", "groupe", "gruppe", "holding", "holdings", "bank", "banque",
                 "banca", "the", "et", "and", "und"}


def _norm_company(text: str) -> str:
    """'Bank Julius Bär & Co. AG' e 'JULIUS BAER' -> 'julius baer';
    'Banque Lombard Odier & Cie SA', 'Lombard Odier Group' -> 'lombard odier'."""
    t = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        t = t.replace(a, b)
    t = re.sub(r"\(.*?\)", " ", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode())
    words = [w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 1 and w not in COMPANY_NOISE]
    return " ".join(words)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"\(.*?\)|\b(m|f|w|h|d|x)\s*/\s*(f|w|h|m|d|x)(\s*/\s*\w)?\b", " ", text.lower())
    text = re.sub(r"\b\d{1,3}\s*-?\s*\d{0,3}\s*%", " ", text)  # "80-100%"
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@dataclass
class Job:
    source: str                     # "linkedin", "indeed", "jobs.ch", "jobup.ch", "apify:moovijob"...
    external_id: str                # id no site de origem
    url: str
    title: str
    company: str
    location: str
    country: str                    # "CH" | "LU" | ...
    description: str = ""
    posted_at: str | None = None
    apply_url: str | None = None    # link direto de candidatura (ATS da empresa), quando houver
    apply_email: str | None = None  # e-mail de candidatura, quando o anúncio publica um
    workload: str | None = None     # "80-100%" (muito comum na Suíça)
    language: str | None = None     # "fr" | "en" | "de" (preenchido na triagem)
    raw: dict = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def dedup_key(self) -> str:
        """Mesma vaga publicada no LinkedIn e no jobs.ch deve virar UMA linha.

        Usamos empresa + título normalizados (sem "(m/f/d)", sem "80-100%").
        """
        return dedup_key(self.company, self.title)


def dedup_key(company: str, title: str) -> str:
    base = f"{_norm_company(company)}|{_norm(title)}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


# ---------- Saídas estruturadas do LLM ----------

class CandidateTriage(BaseModel):
    candidate: str = Field(description="id do candidato, ex.: 'eu' ou 'esposa'")
    relevant: bool = Field(description="vale a pena fazer a análise detalhada?")
    reason: str = Field(description="uma frase explicando")


class Triage(BaseModel):
    job_language: Literal["fr", "en", "de", "other"]
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "executive", "unknown"]
    hard_requirements: list[str] = Field(description="requisitos eliminatórios explícitos (idioma C1, permis, diploma...)")
    candidates: list[CandidateTriage]


class DimensionScore(BaseModel):
    name: Literal["hard_skills", "experience", "domain", "languages", "location_permit", "education"]
    score_current_cv: int = Field(ge=0, le=100, description="aderência usando SÓ o CV atual")
    score_potential: int = Field(ge=0, le=100, description="aderência possível usando o perfil mestre (fatos reais)")
    evidence: str = Field(description="trecho/fato do CV ou perfil que justifica a nota")


class MatchAssessment(BaseModel):
    dimensions: list[DimensionScore]
    desire_fit: int = Field(ge=0, le=100, description="quanto a vaga corresponde ao que o candidato PROCURA (preferências)")
    knockouts: list[str] = Field(description="requisitos eliminatórios que o candidato NÃO cumpre de verdade")
    gaps_fixable_by_tailoring: list[str] = Field(description="o que falta no CV atual mas existe no perfil mestre")
    real_gaps: list[str] = Field(description="o que falta de verdade (não dá para resolver reescrevendo o CV)")
    keywords_to_mirror: list[str] = Field(description="termos do anúncio que devem aparecer no CV adaptado")
    summary: str = Field(description="2-3 frases: por que sim / por que não")


class TailoredDocs(BaseModel):
    cv_markdown: str = Field(description="CV completo adaptado, em Markdown, no idioma da vaga")
    cover_letter_markdown: str = Field(description="carta de motivação/apresentação no idioma da vaga")
    changes_made: list[str] = Field(description="lista do que foi alterado em relação ao CV atual e por quê")
    facts_used: list[str] = Field(description="fatos do perfil mestre usados (para auditoria anti-invenção)")
