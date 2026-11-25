"""LinkedIn, Indeed e Google Jobs via python-jobspy (github.com/speedyapply/JobSpy).

Por que JobSpy e não um scraper próprio de LinkedIn?
- É o scraper aberto mais mantido para LinkedIn/Indeed; lê só a busca PÚBLICA (sem login),
  então não arrisca a sua conta do LinkedIn.
- Indeed tem domínio para Suíça (ch.indeed.com) e Luxemburgo (lu.indeed.com).
- "google" consulta o Google Jobs, que agrega jobup.ch, moovijob, jobs.lu, sites de empresas...
  É uma forma barata de cobrir boards para os quais ainda não temos conector dedicado.

Limites conhecidos: o LinkedIn começa a devolver 429 por volta da 10ª página; por isso
`results_wanted` baixo por termo e muitos termos (a expansão de títulos cuida disso).
"""

from __future__ import annotations

import logging
import math

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

COUNTRY_INDEED = {"CH": "switzerland", "LU": "luxembourg", "FR": "france"}


def _s(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v)


class JobSpySource(Source):
    name = "jobspy"

    def __init__(self, cfg: dict):
        self.sites = cfg.get("sites", ["linkedin", "indeed"])
        self.results_wanted = cfg.get("results_wanted", 25)
        self.hours_old = cfg.get("hours_old", 48)
        self.linkedin_fetch_description = cfg.get("linkedin_fetch_description", True)
        self.proxies = cfg.get("proxies")

    def search(self, query: str, location: str, country: str) -> list[Job]:
        from jobspy import scrape_jobs  # import tardio: é pesado (pandas)

        try:
            df = scrape_jobs(
                site_name=self.sites,
                search_term=query,
                google_search_term=f"{query} jobs near {location} since yesterday",
                location=location,
                results_wanted=self.results_wanted,
                hours_old=self.hours_old,
                country_indeed=COUNTRY_INDEED.get(country, "switzerland"),
                linkedin_fetch_description=self.linkedin_fetch_description,
                description_format="markdown",
                proxies=self.proxies,
                verbose=0,
            )
        except Exception as e:  # um site fora do ar não pode derrubar o pipeline
            log.warning("jobspy falhou para %r/%s: %s", query, location, e)
            return []

        jobs = []
        for r in df.to_dict("records"):
            jobs.append(
                Job(
                    source=_s(r.get("site")) or "jobspy",
                    external_id=_s(r.get("id")),
                    url=_s(r.get("job_url")),
                    apply_url=_s(r.get("job_url_direct")) or None,
                    apply_email=(_s(r.get("emails")).split(",")[0] or None),
                    title=_s(r.get("title")),
                    company=_s(r.get("company")),
                    location=_s(r.get("location")) or location,
                    country=country,
                    description=_s(r.get("description")),
                    posted_at=_s(r.get("date_posted")) or None,
                    raw={k: _s(v) for k, v in r.items() if k != "description"},
                )
            )
        return jobs
