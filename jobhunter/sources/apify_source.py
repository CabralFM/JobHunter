"""Fonte genérica via Apify (apify.com) — scrapers prontos mantidos por terceiros, que rodam
com proxies próprios (por isso funcionam mesmo quando um site bloqueia o seu IP).

Cada "actor" do Apify tem seu próprio formato de entrada e de saída:
- ENTRADA: `input_template` no settings.yaml (JSON). Marcadores substituídos a cada busca:
    {query}               termo de busca
    {location}            cidade (vazio = país inteiro)
    {country}             código do país (CH, LU)
    {country_name}        nome do país em inglês (Switzerland, Luxembourg)
    {linkedin_search_url} URL de busca do LinkedIn para o termo e o país (últimas 48 h)
- SAÍDA: os nomes de campo variam entre actors; `ADAPTIVE_FIELDS` tenta os nomes mais comuns.
  Se um actor usar nomes diferentes, informe `field_map` no settings.yaml.

Requer a variável de ambiente APIFY_TOKEN (no arquivo .env).
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlencode

import httpx

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

COUNTRY_NAME = {"CH": "Switzerland", "LU": "Luxembourg", "FR": "France"}
LINKEDIN_GEO = {"CH": "106693272", "LU": "104042105"}

# nomes de campo mais comuns nos actors de vagas do Apify, em ordem de preferência
ADAPTIVE_FIELDS = {
    "title": ["title", "jobTitle", "positionName", "position", "name"],
    "company": ["companyName", "company", "company_name", "employer", "hiringOrganization"],
    "url": ["link", "url", "jobUrl", "job_url", "jobLink", "detailUrl"],
    "description": ["descriptionText", "description", "jobDescription", "descriptionHtml", "text"],
    "location": ["location", "jobLocation", "place", "city", "formattedLocation"],
    "posted_at": ["postedAt", "publishedAt", "datePosted", "postedDate", "date", "posted_at"],
    "external_id": ["id", "jobId", "job_id", "jobPostingId"],
    "apply_url": ["applyUrl", "apply_url", "applicationUrl", "externalApplyLink"],
    "apply_email": ["applyEmail", "email", "contactEmail"],
}


def linkedin_search_url(query: str, country: str, hours: int = 48) -> str:
    params = {"keywords": query, "location": COUNTRY_NAME.get(country, country),
              "f_TPR": f"r{hours * 3600}", "position": 1, "pageNum": 0}
    if country in LINKEDIN_GEO:
        params["geoId"] = LINKEDIN_GEO[country]
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def _flatten(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):  # ex.: {"name": "Empresa"} ou {"addressLocality": "Genève"}
        for k in ("name", "addressLocality", "text", "value"):
            if v.get(k):
                return str(v[k])
        return ""
    if isinstance(v, list):
        return ", ".join(filter(None, (_flatten(x) for x in v)))
    return str(v)


def pick(item: dict, field: str, field_map: dict) -> str:
    keys = [field_map[field]] if field in field_map else ADAPTIVE_FIELDS.get(field, [field])
    for k in keys:
        val = _flatten(item.get(k))
        if val:
            return val
    return ""


def fill_template(template: str, **values: str) -> dict:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", json.dumps(v)[1:-1])  # escapa aspas/barras para JSON
    return json.loads(out)


class ApifySource(Source):
    def __init__(self, name: str, cfg: dict):
        self.name = cfg.get("source_name") or f"apify:{name}"
        self.actor = cfg["actor"].replace("/", "~")
        self.input_template = json.dumps(cfg.get("input_template", {}), ensure_ascii=False)
        self.field_map = cfg.get("field_map", {})
        self.countries = cfg.get("countries", ["CH", "LU"])
        self.national = cfg.get("national", True)
        self.known_ids = set()
        self.timeout = cfg.get("timeout_seconds", 300)

    @property
    def token(self) -> str | None:
        return os.environ.get("APIFY_TOKEN")

    def search(self, query: str, location: str, country: str) -> list[Job]:
        if country not in self.countries:
            return []
        if not self.token:
            log.warning("%s: APIFY_TOKEN não definido no .env, pulando", self.name)
            return []
        payload = fill_template(self.input_template, query=query, location=location, country=country,
                                country_name=COUNTRY_NAME.get(country, country),
                                linkedin_search_url=linkedin_search_url(query, country))
        url = f"https://api.apify.com/v2/acts/{self.actor}/run-sync-get-dataset-items"
        try:
            r = httpx.post(url, params={"token": self.token}, json=payload, timeout=self.timeout)
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            log.warning("%s falhou: %s", self.name, e)
            return []
        if items and not isinstance(items, list):
            items = [items]
        if items:
            log.debug("%s: campos do primeiro item: %s", self.name, sorted(items[0].keys()))

        jobs = []
        for it in items:
            p = lambda f: pick(it, f, self.field_map)  # noqa: E731
            url_ = p("url")
            if not url_ or not p("title"):
                continue
            jobs.append(Job(
                source=self.name, external_id=p("external_id") or url_, url=url_,
                title=p("title"), company=p("company"), location=p("location") or location or country,
                country=country, description=p("description"), posted_at=p("posted_at") or None,
                apply_url=p("apply_url") or None, apply_email=p("apply_email") or None, raw=it,
            ))
        return jobs
