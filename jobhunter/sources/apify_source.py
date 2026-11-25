"""Fonte genérica via Apify (apify.com) — scrapers prontos mantidos por terceiros.

Por que existe: para jobs.lu, moovijob.com, jobup.ch, job-room.ch etc. já há dezenas de
"actors" no Apify, pagos por uso (tipicamente US$ 0,5–2 por 1.000 vagas). Para uma busca
diária de algumas centenas de vagas isso dá centavos por dia e elimina a manutenção de
scrapers quando o site muda o HTML.

Configuração (settings.yaml):

    apify:
      moovijob:
        enabled: true
        actor: "usuario/nome-do-actor"      # copie do Apify Store
        input_template: {"keyword": "{query}", "location": "{location}", "maxItems": 50}
        field_map: {title: "title", company: "company", url: "url", description: "description",
                    location: "location", external_id: "id", posted_at: "date"}
        countries: ["LU"]

Requer a variável de ambiente APIFY_TOKEN. Cada actor tem um formato de entrada/saída
próprio: rode-o uma vez no site do Apify, veja o JSON e ajuste `input_template`/`field_map`.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)


class ApifySource(Source):
    def __init__(self, name: str, cfg: dict):
        self.name = f"apify:{name}"
        self.actor = cfg["actor"].replace("/", "~")
        self.input_template = json.dumps(cfg.get("input_template", {}))
        self.field_map = cfg.get("field_map", {})
        self.countries = cfg.get("countries", ["CH", "LU"])
        self.token = os.environ.get("APIFY_TOKEN")

    def search(self, query: str, location: str, country: str) -> list[Job]:
        if country not in self.countries:
            return []
        if not self.token:
            log.warning("%s: APIFY_TOKEN não definido, pulando", self.name)
            return []
        payload = json.loads(
            self.input_template.replace("{query}", query.replace('"', "")).replace("{location}", location)
        )
        url = f"https://api.apify.com/v2/acts/{self.actor}/run-sync-get-dataset-items"
        try:
            r = httpx.post(url, params={"token": self.token}, json=payload, timeout=300)
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            log.warning("%s falhou: %s", self.name, e)
            return []

        fm = self.field_map
        g = lambda it, k: str(it.get(fm.get(k, k)) or "")  # noqa: E731
        return [
            Job(
                source=self.name,
                external_id=g(it, "external_id") or g(it, "url"),
                url=g(it, "url"),
                title=g(it, "title"),
                company=g(it, "company"),
                location=g(it, "location") or location,
                country=country,
                description=g(it, "description"),
                posted_at=g(it, "posted_at") or None,
                apply_email=g(it, "apply_email") or None,
                raw=it,
            )
            for it in items
        ]
