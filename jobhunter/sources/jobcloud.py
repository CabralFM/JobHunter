"""jobs.ch e jobup.ch (ambos do grupo JobCloud, mesma plataforma).

jobup.ch é o principal board da Suíça francófona (Genève, Lausanne, Vaud, Fribourg...),
jobs.ch é o maior do país. Os dois sites carregam os resultados por uma API JSON interna.

ATENÇÃO — NÃO VERIFICADO: os endpoints abaixo são os usados pelo front-end público, mas não
são documentados e podem mudar. Não foi possível testá-los no ambiente onde este código foi
escrito. No primeiro uso rode:

    python -m jobhunter probe jobup.ch "data analyst" Lausanne

Se vier vazio/erro, abra o site no navegador, F12 > Network > XHR, faça uma busca e copie a
URL da requisição de busca para `search_url` em settings.yaml. Alternativa sem manutenção:
desative esta fonte e ligue o ator Apify correspondente (seção `apify` do settings).
"""

from __future__ import annotations

import logging
import time

import httpx

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

DEFAULTS = {
    "jobs.ch": {
        "search_url": "https://www.jobs.ch/api/v1/public/search",
        "detail_url": "https://www.jobs.ch/api/v1/public/search/job/{id}",
        "public_url": "https://www.jobs.ch/fr/offres-emplois/detail/{id}/",
    },
    "jobup.ch": {
        "search_url": "https://www.jobup.ch/api/v1/public/search",
        "detail_url": "https://www.jobup.ch/api/v1/public/search/job/{id}",
        "public_url": "https://www.jobup.ch/fr/emplois/detail/{id}/",
    },
}


class JobCloudSource(Source):
    def __init__(self, site: str, cfg: dict):
        self.name = site
        self.urls = {**DEFAULTS[site], **{k: v for k, v in cfg.items() if k.endswith("_url")}}
        self.rows = cfg.get("rows", 40)
        self.max_pages = cfg.get("max_pages", 2)
        self.fetch_details = cfg.get("fetch_details", True)
        self.delay = cfg.get("delay_seconds", 1.5)  # educação com o servidor
        self.http = httpx.Client(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (job-hunter personal use)", "Accept": "application/json"},
            follow_redirects=True,
        )

    def _get(self, url: str, **params) -> dict | None:
        try:
            r = self.http.get(url, params=params or None)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning("%s: falha em %s: %s", self.name, url, e)
            return None

    def search(self, query: str, location: str, country: str) -> list[Job]:
        if country != "CH":
            return []
        jobs: list[Job] = []
        for page in range(1, self.max_pages + 1):
            data = self._get(self.urls["search_url"], query=query, location=location, page=page, rows=self.rows)
            docs = (data or {}).get("documents") or (data or {}).get("results") or []
            if not docs:
                break
            for d in docs:
                jid = str(d.get("job_id") or d.get("id") or "")
                if not jid:
                    continue
                desc = ""
                if self.fetch_details:
                    time.sleep(self.delay)
                    det = self._get(self.urls["detail_url"].format(id=jid)) or {}
                    desc = det.get("template_text") or det.get("description") or det.get("template") or ""
                jobs.append(
                    Job(
                        source=self.name,
                        external_id=jid,
                        url=self.urls["public_url"].format(id=jid),
                        apply_url=d.get("application_url") or None,
                        title=d.get("title", ""),
                        company=(d.get("company_name") or (d.get("company") or {}).get("name") or ""),
                        location=d.get("place") or location,
                        country="CH",
                        workload=(f"{d['employment_grade_min']}-{d['employment_grade_max']}%"
                                  if d.get("employment_grade_min") else None),
                        description=desc or d.get("preview", ""),
                        posted_at=d.get("publication_date"),
                        raw=d,
                    )
                )
            time.sleep(self.delay)
        return jobs
