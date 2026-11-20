"""Geography, title pre-filter, parsing, deduplication."""

from __future__ import annotations

import pytest

from jobradar.common import canonical_url, load_config, parse_date, strip_html
from jobradar.etl.dedup import ExistingIndex, dedup_jobs
from jobradar.etl.geo_filter import GeoFilter
from jobradar.etl.text import title_tokens
from jobradar.etl.title_prefilter import prefilter_title, title_priority
from jobradar.models import RawJob
from jobradar.sources.eudatajobs import parse_card
from jobradar.sources.randstad import format_salary, parse_jobposting, slug_to_title
from jobradar.sources.weworkremotely import WeWorkRemotelyScraper

CFG = load_config()  # config/sources.yaml: home = Milan, Turin hybrid exception


def job(title="Revenue Operations Analyst", company="Acme", loc=None, mode=None, desc="", url=None, source="himalayas", **md):
    return RawJob(source=source, url_origin=url or f"https://example.com/{title.replace(' ', '-')}-{company}",
                  job_title=title, company_name=company, location_raw=loc, location_type=mode, description_raw=desc, metadata=md)


@pytest.fixture(scope="module")
def geo():
    return GeoFilter(CFG)


# ---------------- geography ----------------
def test_bergamo_on_site_inside(geo):
    d = geo.decide(job(loc="Bergamo, Lombardy, Italy", mode="on-site", countries=["it"], cities=["Bergamo"]))
    assert d.keep and 35 < d.km < 50


def test_turin_hybrid_inside_by_exception(geo):
    d = geo.decide(job(loc="Turin, Piedmont, Italy", mode="hybrid", countries=["it"]))
    assert d.keep and "exception" in d.reason and d.km > 100


def test_turin_on_site_outside(geo):
    assert not geo.decide(job(loc="Turin, Italy", mode="on-site", countries=["it"])).keep


def test_verona_hybrid_outside(geo):
    assert not geo.decide(job(loc="Verona, Veneto, Italy", mode="hybrid", countries=["it"])).keep


def test_brescia_hybrid_inside(geo):
    d = geo.decide(job(loc="Brescia, Italia", mode="hybrid"))
    assert d.keep and 75 < d.km < 90


def test_amsterdam_on_site_outside(geo):
    d = geo.decide(job(loc="Amsterdam, Netherlands", mode="on-site", countries=["nl"]))
    assert not d.keep and "outside the accepted countries" in d.reason


def test_amsterdam_hybrid_text_only_outside(geo):
    assert not geo.decide(job(loc="Amsterdam, Netherlands (Hybrid)")).keep


def test_netherlands_remote_inside(geo):
    assert geo.decide(job(loc="Netherlands", mode="remote", countries_named=["netherlands"])).keep


def test_us_only_remote_outside(geo):
    assert not geo.decide(job(loc="United States", mode="remote", countries=["us"], regions=["north_america"])).keep


def test_latam_regions_outside(geo):
    assert not geo.decide(job(loc="Argentina", mode="remote", countries=["ar", "bo"], regions=["latam", "north_america"])).keep


def test_us_only_from_description(geo):
    assert not geo.decide(job(loc="", mode="remote", desc="You must be based in the United States to apply.")).keep


def test_european_country_list_is_remote_not_a_city(geo):
    d = geo.decide(job(loc="Albania, Andorra, Austria, Belgium, San Marino, Spain", mode="remote",
                       countries=["al", "ad", "at", "sm"], regions=["eu"]))
    assert d.keep and d.mode == "remote" and d.km is None


def test_uk_only_kept_with_note(geo):
    d = geo.decide(job(loc="UK", mode="remote", countries=["gb"], regions=["uk"]))
    assert d.keep and d.notes


def test_unknown_place_rejected_without_geocoder(geo):
    assert not geo.decide(job(loc="Nowhereville", mode="on-site")).keep


def test_geocoder_can_resolve_unknown_city():
    class Res:
        latitude, longitude, country_iso2 = 45.50, 9.20, "IT"

    class FakeGeocoder:
        def geocode(self, q):
            return Res()

    d = GeoFilter(CFG, geocoder=FakeGeocoder()).decide(job(loc="Sesto San Giovanni", mode="on-site"))
    assert d.keep and d.km < 15


# ---------------- title pre-filter ----------------
@pytest.mark.parametrize("title", [
    "Revenue Operations Analyst", "RevOps Specialist", "Sales Operations & Enablement Specialist", "CRM Specialist",
    "Salesforce Developer", "GTM Engineer", "Business Intelligence Analyst", "Marketing Operations Manager",
    "Head of Revenue Operations", "Tecnico commerciale CRM", "Addetto CRM e marketing",
])
def test_prefilter_keeps(title):
    assert prefilter_title(title).keep, title


@pytest.mark.parametrize("title", [
    "Senior Software Engineer", "Full Stack Developer", "Director, EMEA Sales Operations", "VP Revenue Operations",
    "Addetto alle vendite GDO", "Infermiere", "Agente di commercio", "GTM Recruiter", "Content Creator",
    "Customer Service Representative",
])
def test_prefilter_drops(title):
    assert not prefilter_title(title).keep, title


def test_title_priority():
    assert title_priority("RevOps Analyst") == 1
    assert title_priority("CRM Manager") == 2
    assert title_priority("Data Analyst") == 3


# ---------------- parsing ----------------
def test_wwr_item_parsing():
    item = """<title>ClickCo Inc.: RevOps Manager</title><region>Anywhere in the World</region><country></country>
    <type>Full-Time</type><description>&lt;p&gt;Hello &lt;strong&gt;world&lt;/strong&gt;&lt;/p&gt;</description>
    <pubDate>Wed, 19 Nov 2025 10:00:00 +0000</pubDate><link>https://weworkremotely.com/remote-jobs/clickco-revops-manager</link>"""
    j = WeWorkRemotelyScraper(config=CFG).parse_item(item)
    assert j.company_name == "ClickCo Inc." and j.job_title == "RevOps Manager"
    assert j.description_raw == "Hello world" and j.location_type == "remote" and j.date_posted.isoformat() == "2025-11-19"


def test_randstad_jobposting():
    html = """<script type="application/ld+json">{"@type":"JobPosting","title":"Controller","datePosted":"2026-10-27T09:36:25+0000",
    "jobLocation":{"@type":"Place","address":{"addressLocality":"Monza","addressRegion":"Lombardy"}},
    "baseSalary":{"currency":"EUR","value":{"minValue":30000,"maxValue":35000,"unitText":"YEAR"}}}</script>"""
    jp = parse_jobposting(html)
    assert jp["title"] == "Controller" and format_salary(jp["baseSalary"]) == "30000-35000 EUR YEAR"
    assert slug_to_title("crm-specialist-fmnb") == "crm specialist"


def test_common_helpers():
    assert canonical_url("https://x.com/a?utm_source=api&id=3#top") == "https://x.com/a?id=3"
    assert parse_date(1795107711).year == 2026
    assert parse_date("2026-11-19T18:20:33Z").isoformat() == "2026-11-19"
    assert strip_html("<p>a</p><ul><li>b</li></ul>") == "a\n\nb"


def test_eudatajobs_card():
    from bs4 import BeautifulSoup

    html = """<div class="job flex flex-col"><a href="https://careers.example.com/bi-analyst?source=eudatajobs_com">
    <h2> Business Intelligence Analyst - Tableau </h2><h3><i class="fas fa-landmark"></i> Heavy Machines Inc. &nbsp;
    <i class="fas fa-map-marker-alt"></i> Slovakia (On-site) </h3></a>
    <div><div><i class="far fa-calendar-alt"></i> Fri Oct 16 2026 </div></div></div>"""
    j = parse_card(BeautifulSoup(html, "html.parser").select_one("div.job"))
    assert j.job_title == "Business Intelligence Analyst - Tableau" and j.company_name == "Heavy Machines Inc."
    assert j.location_raw == "Slovakia" and j.location_type == "on-site" and j.date_posted.isoformat() == "2026-10-16"


# ---------------- deduplication ----------------
def test_dedup_cross_source_keeps_longest():
    a = job("Revenue Operations Manager", "Improvado", url="https://a.example/x", desc="short")
    b = job("Revenue Operations Manager", "Improvado", url="https://b.example/y", desc="much longer text " * 10, source="jobicy")
    kept, dropped = dedup_jobs([a, b], ExistingIndex())
    assert len(kept) == 1 and kept[0].source == "jobicy" and dropped[0][1].startswith("duplicate")


def test_dedup_against_spreadsheet():
    idx = ExistingIndex(urls={canonical_url("https://seen.com/1?utm_source=x")},
                        jobs=[("smartco", title_tokens("Sales Operations & Enablement Specialist"))])
    a = job("CRM Analyst", "Foo", url="https://seen.com/1")
    b = job("Sales Operations Enablement Specialist", "SmartCo")
    c = job("RevOps Analyst", "Bar")
    kept, dropped = dedup_jobs([a, b, c], idx)
    assert [k.company_name for k in kept] == ["Bar"]
    assert {m for _, m in dropped} == {"already in the spreadsheet", "already in the spreadsheet (same company and title)"}
