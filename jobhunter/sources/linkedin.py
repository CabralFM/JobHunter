"""LinkedIn — busca pública de vagas (sem login: a sua conta nunca é usada nem arriscada).

Usa os mesmos endpoints que a página pública linkedin.com/jobs carrega no navegador:
    lista:   /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=...&geoId=...&f_TPR=...&start=N
    detalhe: /jobs-guest/jobs/api/jobPosting/{id}

Por que é diferente da tentativa anterior (JobSpy), que levou erro 429 logo de cara:
1. curl_cffi com `impersonate="chrome"`: a conexão tem a "impressão digital" TLS de um Chrome
   real. Bibliotecas Python comuns são reconhecidas e bloqueadas na hora.
2. Ritmo humano: pausas aleatórias entre pedidos e uma visita à página pública antes (cookies).
3. Ao receber 429, espera e tenta de novo (30 s, 90 s, 180 s) em vez de desistir.
4. Busca por PAÍS (geoId) e só vagas das últimas 48 h: poucos pedidos por dia.
5. Não baixa de novo o detalhe de vagas que já estão no banco.
6. Se mesmo assim o LinkedIn bloquear, passa para o actor do Apify configurado em
   `apify_fallback` (que usa proxies próprios), se houver APIFY_TOKEN no .env.
7. Opcional: LINKEDIN_PROXY no .env (ex.: http://usuario:senha@host:porta) para usar um proxy.
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import random
import re
import time

from ..models import Job
from .apify_source import COUNTRY_NAME, LINKEDIN_GEO, ApifySource
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://www.linkedin.com"
SEARCH = BASE + "/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = BASE + "/jobs-guest/jobs/api/jobPosting/{id}"

CARD_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
ID_RE = re.compile(r"urn:li:jobPosting:(\d+)")
TITLE_RE = re.compile(r'class="base-search-card__title"[^>]*>\s*(.*?)\s*</h3>', re.S)
COMPANY_RE = re.compile(r'class="base-search-card__subtitle"[^>]*>\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>', re.S)
LOCATION_RE = re.compile(r'class="job-search-card__location"[^>]*>\s*(.*?)\s*</span>', re.S)
DATE_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
DESC_RE = re.compile(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return html_lib.unescape(TAG_RE.sub("", s or "")).strip()


def parse_search(page: str) -> list[dict]:
    """Extrai os cartões de vaga do HTML da lista (sem dependência de BeautifulSoup)."""
    cards = []
    for block in CARD_RE.findall(page):
        m = ID_RE.search(block)
        t = TITLE_RE.search(block)
        if not (m and t):
            continue
        c, loc, d = COMPANY_RE.search(block), LOCATION_RE.search(block), DATE_RE.search(block)
        cards.append({
            "id": m.group(1),
            "title": _text(t.group(1)),
            "company": _text(c.group(1)) if c else "",
            "location": _text(loc.group(1)) if loc else "",
            "posted_at": d.group(1) if d else None,
        })
    return cards


def parse_detail(page: str) -> str:
    m = DESC_RE.search(page)
    if not m:
        return ""
    try:
        from markdownify import markdownify
        return markdownify(m.group(1), heading_style="ATX").strip()
    except ImportError:
        return _text(m.group(1))


class Blocked(RuntimeError):
    pass


class LinkedInSource(Source):
    name = "linkedin"
    national = True

    def __init__(self, cfg: dict):
        self.max_pages = cfg.get("max_pages", 2)            # 25 vagas por página
        self.hours_old = cfg.get("hours_old", 48)
        self.delay = tuple(cfg.get("delay_seconds", [4.0, 8.0]))
        self.backoff = cfg.get("backoff_seconds", [30, 90, 180])
        self.max_details = cfg.get("max_details_per_run", 200)
        self.known_ids = set()
        self.blocked = False
        self.details_done = 0
        self.deferred = 0
        self.fallback = ApifySource("linkedin", {**cfg["apify_fallback"], "source_name": "linkedin"}) \
            if cfg.get("apify_fallback", {}).get("actor") else None
        self._session = None

    # ---------- HTTP ----------
    def session(self):
        if self._session is None:
            from curl_cffi import requests as creq
            proxy = os.environ.get("LINKEDIN_PROXY")
            self._session = creq.Session(impersonate="chrome",
                                         proxies={"https": proxy, "http": proxy} if proxy else None)
            self._session.headers.update({"Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8"})
            try:  # visita a página pública primeiro, como um navegador (recebe cookies)
                self._session.get(BASE + "/jobs/", timeout=30)
            except Exception as e:
                log.debug("linkedin: aquecimento falhou: %s", e)
        return self._session

    def _get(self, url: str, **params) -> str | None:
        time.sleep(random.uniform(*self.delay))
        for attempt, wait in enumerate([0, *self.backoff]):
            if wait:
                log.info("linkedin: 429, aguardando %ss (tentativa %d)", wait, attempt + 1)
                time.sleep(wait + random.uniform(0, 10))
            try:
                r = self.session().get(url, params=params or None, timeout=30)
            except Exception as e:
                log.warning("linkedin: erro de rede: %s", e)
                continue
            if r.status_code == 200:
                return r.text
            if r.status_code in (404, 410):
                return None
            if r.status_code not in (429, 999, 403):
                log.warning("linkedin: HTTP %s em %s", r.status_code, url)
                return None
        raise Blocked("LinkedIn bloqueou mesmo após as novas tentativas")

    # ---------- busca ----------
    def search(self, query: str, location: str, country: str) -> list[Job]:
        if self.blocked:
            return self.fallback.search(query, location, country) if self.fallback else []
        try:
            return self._search(query, location, country)
        except Blocked as e:
            self.blocked = True
            log.warning("%s — %s", e, "usando o Apify a partir de agora" if self.fallback
                        else "sem APIFY_TOKEN/apify_fallback; LinkedIn pulado nesta execução")
            return self.fallback.search(query, location, country) if self.fallback else []

    def _search(self, query: str, location: str, country: str) -> list[Job]:
        jobs: list[Job] = []
        for page in range(self.max_pages):
            params = {"keywords": query, "location": location or COUNTRY_NAME.get(country, country),
                      "f_TPR": f"r{self.hours_old * 3600}", "start": page * 25}
            if not location and country in LINKEDIN_GEO:
                params["geoId"] = LINKEDIN_GEO[country]
            page_html = self._get(SEARCH, **params)
            cards = parse_search(page_html or "")
            if not cards:
                break
            for c in cards:
                if c["id"] in self.known_ids:
                    continue
                if self.details_done >= self.max_details:
                    # cota de descrições do dia esgotada: NÃO grava a vaga (sem descrição ela seria
                    # descartada para sempre); fica para a próxima execução
                    self.deferred += 1
                    continue
                desc = parse_detail(self._get(DETAIL.format(id=c["id"])) or "")
                self.details_done += 1
                if not desc:
                    continue  # detalhe falhou: tenta de novo na próxima execução
                self.known_ids.add(c["id"])
                jobs.append(Job(
                    source="linkedin", external_id=c["id"], url=f"{BASE}/jobs/view/{c['id']}/",
                    title=c["title"], company=c["company"], location=c["location"] or country,
                    country=country, description=desc, posted_at=c["posted_at"],
                ))
            if len(cards) < 25:
                break
        return jobs
