"""Fontes de vagas. Cada fonte implementa `search(query, location, country) -> list[Job]`.

Para adicionar um site novo (ex.: jobs.lu), crie um módulo aqui e registre em `build_sources`.
"""

from __future__ import annotations

import logging

from .base import Source

log = logging.getLogger(__name__)


def build_sources(settings: dict) -> list[Source]:
    from .apify_source import ApifySource
    from .jobcloud import JobCloudSource
    from .jobspy_source import JobSpySource
    from .jsonld import JsonLdSiteSource
    from .linkedin import LinkedInSource

    out: list[Source] = []
    cfg = settings.get("sources", {})
    if cfg.get("jobspy", {}).get("enabled"):
        out.append(JobSpySource(cfg["jobspy"]))
    if cfg.get("linkedin", {}).get("enabled"):
        out.append(LinkedInSource(cfg["linkedin"]))
    for name, site_cfg in (cfg.get("sites") or {}).items():
        if site_cfg.get("enabled"):
            out.append(JsonLdSiteSource(name, site_cfg))
    for site in ("jobs.ch", "jobup.ch"):
        if cfg.get(site, {}).get("enabled"):
            out.append(JobCloudSource(site, cfg[site]))
    for name, actor_cfg in (cfg.get("apify") or {}).items():
        if actor_cfg.get("enabled"):
            out.append(ApifySource(name, actor_cfg))
    log.info("fontes ativas: %s", [s.name for s in out])
    return out
