"""Fonte genérica para sites que publicam o "cartão de vaga" schema.org (JSON-LD JobPosting).

Quase todo site de vagas publica, dentro da página de cada anúncio, um bloco
<script type="application/ld+json"> com {"@type": "JobPosting", "title": ..., "description": ...}
— é exigência do Google Jobs para indexar a vaga. Ler esse bloco é muito mais estável do que
depender do layout HTML do site (que muda sem aviso).

Funcionamento:
1. abre a página de resultados do site para o termo (`search_url`, com {query});
2. coleta os links de anúncios (`link_regex`);
3. abre cada anúncio novo e lê o JobPosting.

Usado para moovijob.com e jobs.lu (Luxemburgo). Para outro site: copie um bloco em
settings.yaml -> sources -> sites e ajuste `search_url` e `link_regex`.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import random
import re
import time
from urllib.parse import quote_plus, urljoin

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
HREF_RE = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _walk(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _walk(x)
    elif isinstance(obj, dict):
        yield obj
        for k in ("@graph", "itemListElement", "item"):
            if k in obj:
                yield from _walk(obj[k])


def find_job_posting(page: str) -> dict | None:
    for raw in JSONLD_RE.findall(page):
        try:
            data = json.loads(html_lib.unescape(raw.strip()), strict=False)
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return node
    return None


def _name(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or "")
    if isinstance(v, list) and v:
        return _name(v[0])
    return str(v or "")


def _location(v) -> str:
    places = v if isinstance(v, list) else [v]
    out = []
    for p in places:
        if isinstance(p, dict):
            a = p.get("address") or {}
            if isinstance(a, dict):
                out.append(a.get("addressLocality") or a.get("addressRegion") or "")
            else:
                out.append(str(a))
    return ", ".join(x for x in out if x)


def _to_markdown(html: str) -> str:
    html = html_lib.unescape(html or "")
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX").strip()
    except ImportError:
        return TAG_RE.sub(" ", html).strip()


def job_from_jsonld(node: dict, url: str, source: str, country: str) -> Job:
    own_url = node.get("url")
    return Job(
        source=source, external_id=url, url=url,  # a URL do anúncio é o id (evita baixar de novo)
        title=html_lib.unescape(str(node.get("title") or "")).strip(),
        company=_name(node.get("hiringOrganization")),
        location=_location(node.get("jobLocation")) or country, country=country,
        description=_to_markdown(node.get("description", "")),
        posted_at=node.get("datePosted"),
        apply_url=own_url if own_url and own_url != url else None,
        raw={k: v for k, v in node.items() if k != "description"},
    )


class JsonLdSiteSource(Source):
    national = True

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.search_url = cfg["search_url"]
        self.link_re = re.compile(cfg["link_regex"])
        self.countries = cfg.get("countries", ["LU"])
        self.max_links = cfg.get("max_links_per_search", 30)
        self.delay = tuple(cfg.get("delay_seconds", [1.0, 2.5]))
        self.known_ids = set()  # aqui guardamos URLs já vistas (external_id pode ser a URL)
        self._session = None

    def session(self):
        if self._session is None:
            from curl_cffi import requests as creq
            self._session = creq.Session(impersonate="chrome")
            self._session.headers.update({"Accept-Language": "fr-LU,fr;q=0.9,en;q=0.8"})
        return self._session

    def _get(self, url: str) -> str:
        time.sleep(random.uniform(*self.delay))
        try:
            r = self.session().get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            log.warning("%s: HTTP %s em %s", self.name, r.status_code, url)
        except Exception as e:
            log.warning("%s: erro em %s: %s", self.name, url, e)
        return ""

    def links(self, page: str, base: str) -> list[str]:
        seen = []
        for href in HREF_RE.findall(page):
            full = urljoin(base, html_lib.unescape(href))
            if self.link_re.search(full) and full not in seen:
                seen.append(full)
        return seen[: self.max_links]

    def search(self, query: str, location: str, country: str) -> list[Job]:
        if country not in self.countries:
            return []
        url = self.search_url.replace("{query}", quote_plus(query))
        page = self._get(url)
        jobs = []
        for link in self.links(page, url):
            if link in self.known_ids:
                continue
            node = find_job_posting(self._get(link))
            self.known_ids.add(link)
            if node:
                jobs.append(job_from_jsonld(node, link, self.name, country))
        if page and not jobs:
            log.info("%s: nenhum anúncio novo para %r (links encontrados: %d)", self.name, query,
                     len(self.links(page, url)))
        return jobs
