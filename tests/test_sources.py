"""Testes dos leitores de LinkedIn, JSON-LD (moovijob/jobs.lu) e Apify — sem rede."""

from __future__ import annotations

from jobhunter.sources import apify_source, jsonld, linkedin

LI_SEARCH = """
<li>
  <div class="base-card relative w-full" data-entity-urn="urn:li:jobPosting:4012345678">
    <a class="base-card__full-link" href="https://ch.linkedin.com/jobs/view/data-analyst-at-x-4012345678"></a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">
            Data Quality Analyst (80-100%)
      </h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="https://ch.linkedin.com/company/x">
          Banque Privée &amp; Cie
        </a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">
          Geneva, Geneva, Switzerland
        </span>
        <time class="job-search-card__listdate" datetime="2026-11-24">1 day ago</time>
      </div>
    </div>
  </div>
</li>
<li><div class="base-card" data-entity-urn="urn:li:jobPosting:4099999999">
  <h3 class="base-search-card__title">SAP MM Consultant</h3>
  <h4 class="base-search-card__subtitle">Consulting SA</h4>
  <span class="job-search-card__location">Zürich</span></div></li>
"""

LI_DETAIL = """<section><div class="show-more-less-html__markup relative overflow-hidden">
<p>Vous serez responsable de la <strong>qualité des données</strong>.</p><ul><li>SQL</li><li>Power BI</li></ul>
</div></section>"""


def test_linkedin_parsers():
    cards = linkedin.parse_search(LI_SEARCH)
    assert [c["id"] for c in cards] == ["4012345678", "4099999999"]
    assert cards[0]["title"] == "Data Quality Analyst (80-100%)"
    assert cards[0]["company"] == "Banque Privée & Cie"
    assert cards[0]["location"] == "Geneva, Geneva, Switzerland"
    assert cards[0]["posted_at"] == "2026-11-24"
    assert cards[1]["company"] == "Consulting SA"
    desc = linkedin.parse_detail(LI_DETAIL)
    assert "qualité des données" in desc and "Power BI" in desc


def test_linkedin_search_skips_known_and_falls_back_when_blocked(monkeypatch):
    src = linkedin.LinkedInSource({"max_pages": 1})
    src.known_ids = {"4099999999"}
    pages = {linkedin.SEARCH: LI_SEARCH}
    monkeypatch.setattr(src, "_get", lambda url, **p: pages.get(url, LI_DETAIL))
    jobs = src.search("data quality", "", "CH")
    assert [j.external_id for j in jobs] == ["4012345678"]          # a conhecida foi pulada
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4012345678/"
    assert "Power BI" in jobs[0].description

    # bloqueado: sem fallback devolve vazio e não tenta mais
    def blocked(url, **p):
        raise linkedin.Blocked("429")
    src2 = linkedin.LinkedInSource({})
    monkeypatch.setattr(src2, "_get", blocked)
    assert src2.search("x", "", "CH") == [] and src2.blocked

    # bloqueado com fallback do Apify configurado: delega
    src3 = linkedin.LinkedInSource({"apify_fallback": {"actor": "user/linkedin-jobs"}})
    monkeypatch.setattr(src3, "_get", blocked)
    monkeypatch.setattr(src3.fallback, "search", lambda q, l, c: ["via-apify"])
    assert src3.search("x", "", "CH") == ["via-apify"]


JOB_PAGE = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"BreadcrumbList"}</script>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting","title":"Data Analyst (F/H)",
 "description":"&lt;p&gt;Analyse des donn\\u00e9es &lt;b&gt;SQL&lt;/b&gt;&lt;/p&gt;",
 "datePosted":"2026-11-04","hiringOrganization":{"@type":"Organization","name":"TMC Luxembourg"},
 "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Luxembourg"}}]}
</script></head><body></body></html>"""

LIST_PAGE = """<a href="/offres-emploi/tmc-luxembourg/data-analyst-fh">x</a>
<a href="/offres-emploi/tmc-luxembourg/data-analyst-fh">dup</a>
<a href="/a-propos">nav</a>"""


def test_jsonld_site(monkeypatch):
    node = jsonld.find_job_posting(JOB_PAGE)
    assert node["title"] == "Data Analyst (F/H)"
    src = jsonld.JsonLdSiteSource("moovijob", {
        "search_url": "https://www.moovijob.com/offres-emploi?q={query}",
        "link_regex": r"moovijob\.com/offres-emploi/[^/?#]+/[^/?#]+$", "countries": ["LU"]})
    monkeypatch.setattr(src, "_get", lambda url: LIST_PAGE if "?q=" in url else JOB_PAGE)
    jobs = src.search("data analyst", "", "LU")
    assert len(jobs) == 1
    j = jobs[0]
    assert (j.title, j.company, j.location) == ("Data Analyst (F/H)", "TMC Luxembourg", "Luxembourg")
    assert j.url == "https://www.moovijob.com/offres-emploi/tmc-luxembourg/data-analyst-fh"
    assert "SQL" in j.description and "données" in j.description
    assert src.search("data analyst", "", "CH") == []                 # só LU
    assert src.search("data analyst", "", "LU") == []                 # já vista, não baixa de novo


def test_apify_adaptive_fields_and_template():
    item = {"jobTitle": "Business Analyst", "companyName": "Pictet", "jobUrl": "https://x/1",
            "descriptionText": "desc", "location": {"addressLocality": "Genève"}, "postedAt": "2026-11-20"}
    assert apify_source.pick(item, "title", {}) == "Business Analyst"
    assert apify_source.pick(item, "location", {}) == "Genève"
    assert apify_source.pick({"x_title": "T"}, "title", {"title": "x_title"}) == "T"
    payload = apify_source.fill_template(
        '{"urls": ["{linkedin_search_url}"], "q": "{query}"}', query='SAP "MM"',
        linkedin_search_url=apify_source.linkedin_search_url("SAP MM", "CH"))
    assert payload["q"] == 'SAP "MM"'
    assert "geoId=106693272" in payload["urls"][0] and "keywords=SAP+MM" in payload["urls"][0]
