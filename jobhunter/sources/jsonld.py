"""Fonte genérica para sites que publicam o "cartão de vaga" schema.org (JSON-LD JobPosting).

Quase todo site de vagas publica, dentro da página de cada anúncio, um bloco
<script type="application/ld+json"> com {"@type": "JobPosting", "title": ..., "description": ...}
— é exigência do Google Jobs para indexar a vaga. Ler esse bloco é muito mais estável do que
depender do layout HTML do site (que muda sem aviso).

Funcionamento:
1. abre a página de resultados do site para o termo (`search_url`, com {query});
2. coleta os links de anúncios (`link_regex`);
3. abre cada anúncio novo e lê o JobPosting.

Dois modos de achar os anúncios:
- `search_url`: página de resultados do site. Marcadores: {query} (termo codificado para URL)
  e {query_slug} (termo em minúsculas com hífens, ex.: "data-analyst"), como em
  https://en.jobs.lu/{query_slug}-jobs
- `sitemap_url`: o índice (sitemap XML) com TODOS os anúncios do site e a data de atualização de
  cada um. Pega os da janela de idade (max_age_days) cujo endereço contém as palavras do termo.
  Mais estável que a busca do site (moovijob publica moovijob.com/sitemap-joboffers).

Usado para moovijob.com e jobs.lu (Luxemburgo). Para outro site: copie um bloco em
settings.yaml -> sources -> sites e ajuste `search_url`/`sitemap_url` e `link_regex`.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import random
import re
import time
import unicodedata
from datetime import date, timedelta
from urllib.parse import quote_plus, urljoin

from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
HREF_RE = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")
LOC_RE = re.compile(r"<url>(.*?)</url>", re.S | re.I)
SITEMAP_RE = re.compile(r"<sitemap>(.*?)</sitemap>", re.S | re.I)
LOCTAG_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S | re.I)
LASTMOD_RE = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.S | re.I)
STOPWORDS = {"de", "des", "du", "la", "le", "les", "et", "en", "of", "and", "the", "d", "l",
             "h", "f", "m", "w", "x", "fh", "hf", "mf", "fm", "mfd", "mwd"}


def ascii_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in STOPWORDS]


def slugify(text: str) -> str:
    return "-".join(re.split(r"[^a-z0-9]+", unicodedata.normalize("NFKD", text).encode(
        "ascii", "ignore").decode().lower())).strip("-")


def parse_sitemap(xml: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Devolve ([(url, lastmod)], [sub-sitemaps]) de um sitemap ou índice de sitemaps."""
    subs = [m.group(1) for blk in SITEMAP_RE.findall(xml) if (m := LOCTAG_RE.search(blk))]
    urls = []
    for blk in LOC_RE.findall(xml):
        loc, lm = LOCTAG_RE.search(blk), LASTMOD_RE.search(blk)
        if loc:
            urls.append((html_lib.unescape(loc.group(1)), lm.group(1) if lm else None))
    return urls, subs


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
        self.search_url = cfg.get("search_url")
        self.sitemap_url = cfg.get("sitemap_url")
        self.link_re = re.compile(cfg["link_regex"])
        self.max_age_days = cfg.get("max_age_days")
        self._sitemap: list[tuple[str, str | None]] | None = None
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
            (log.debug if r.status_code == 404 else log.warning)("%s: HTTP %s em %s", self.name, r.status_code, url)
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

    def sitemap_entries(self) -> list[tuple[str, str | None]]:
        """Baixa o sitemap uma vez por execução (segue índices de sitemaps)."""
        if self._sitemap is None:
            entries, queue, seen = [], [self.sitemap_url], set()
            while queue and len(seen) < 50:
                u = queue.pop(0)
                if u in seen:
                    continue
                seen.add(u)
                urls, subs = parse_sitemap(self._get(u))
                entries += urls
                queue += subs
            cutoff = (date.today() - timedelta(days=self.max_age_days)).isoformat() if self.max_age_days else None
            self._sitemap = [(u, lm) for u, lm in entries
                             if self.link_re.search(u) and (not cutoff or not lm or lm[:10] >= cutoff)]
            log.info("%s: %d anúncios no sitemap dentro da janela de idade", self.name, len(self._sitemap))
        return self._sitemap

    def candidate_links(self, query: str) -> tuple[list[str], bool]:
        if self.sitemap_url:
            words = ascii_words(query)
            hits = [u for u, _ in self.sitemap_entries()
                    if words and all(w in ascii_words(u.rsplit("/", 2)[-2] + " " + u.rsplit("/", 1)[-1])
                                     for w in words)]
            return hits[: self.max_links], bool(self._sitemap)
        url = self.search_url.replace("{query_slug}", slugify(query)).replace("{query}", quote_plus(query))
        page = self._get(url)
        return self.links(page, url), bool(page)

    def search(self, query: str, location: str, country: str) -> list[Job]:
        if country not in self.countries:
            return []
        links, got_page = self.candidate_links(query)
        jobs = []
        for link in links:
            if link in self.known_ids:
                continue
            node = find_job_posting(self._get(link))
            self.known_ids.add(link)
            if node:
                jobs.append(job_from_jsonld(node, link, self.name, country))
        if got_page and not jobs:
            log.info("%s: nenhum anúncio novo para %r (links candidatos: %d)", self.name, query, len(links))
        return jobs
