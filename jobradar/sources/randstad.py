"""Randstad Italia: local source, reads schema.org JobPosting data from detail pages.

Disabled by default: check the site terms before enabling it. Randstad filters with path segments,
not parameters: /offerte-lavoro/<city>/q-<slug>/. Each ad has the URL
/offerte-lavoro/<title>_<city>_<uuid>/ and structured data in the detail page.
robots.txt allowed /offerte-lavoro/ at the time of writing.

Search logic adapted from the MIT-licensed repository andreadorizza/ai-job-search-italy
(.agents/skills/randstad-search), rewritten in Python.

To avoid opening useless pages, the title pre-filter is applied to the URL slug before downloading a detail.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jobradar.common import parse_date, strip_html
from jobradar.etl.title_prefilter import prefilter_title
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.randstad.it"
DETAIL_RE = re.compile(
    r"/offerte-lavoro/([a-z0-9-]+)_([a-z0-9-]+)_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
)
LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
GENDER_SUFFIX = re.compile(r"\s*-?\s*\b(mfnb|fmnb|mf|fm|m-f-nb|f-m-nb)\b\s*$", re.I)


def slug_to_title(slug: str) -> str:
    return GENDER_SUFFIX.sub("", slug.replace("-", " ")).strip()


def parse_jobposting(html: str) -> dict[str, Any] | None:
    """First schema.org JobPosting block of the page."""
    for block in LDJSON_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for it in data if isinstance(data, list) else [data]:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                return it
    return None


def format_salary(base: Any) -> str | None:
    if not isinstance(base, dict):
        return None
    val = base.get("value") or {}
    if not isinstance(val, dict):
        return None
    lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText") or ""
    if lo is None and hi is None and val.get("value") is None:
        return None
    if lo is None and hi is None:
        return f"{val.get('value')} {base.get('currency', 'EUR')} {unit}".strip()
    return f"{lo or ''}-{hi or ''} {base.get('currency', 'EUR')} {unit}".strip()


class RandstadScraper(BaseScraper):
    SOURCE_NAME = "randstad"
    DEFAULT_DELAY = 2.5
    ACCEPT = "text/html"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        src = self.source_config()
        self.locations: list[str] = src.get("locations", [])
        self.queries: list[str] = src.get("queries", [])
        self.max_details = int(src.get("max_details_per_run", 40))

    def fetch_jobs(self) -> list[RawJob]:
        candidates: dict[str, tuple[str, str]] = {}
        for loc in self.locations:
            for q in self.queries:
                url = f"{BASE_URL}/offerte-lavoro/{loc}/q-{q}/"
                self._sleep_between_requests()
                try:
                    html = self._request_with_retry(url).text
                except Exception as e:
                    logger.warning("Randstad: error on %s: %s", url, e)
                    continue
                for m in DETAIL_RE.finditer(html):
                    candidates.setdefault(f"{BASE_URL}{m.group(0)}", (slug_to_title(m.group(1)), m.group(2).replace("-", " ")))

        relevant = [(u, t, c) for u, (t, c) in candidates.items() if prefilter_title(t).keep]
        logger.info("Randstad: %d distinct ads, %d with a relevant title", len(candidates), len(relevant))
        jobs: list[RawJob] = []
        for url, slug_title, slug_city in relevant[: self.max_details]:
            self._sleep_between_requests()
            try:
                jp = parse_jobposting(self._request_with_retry(url).text)
            except Exception as e:
                logger.warning("Randstad: detail not read %s: %s", url, e)
                jp = None
            jobs.append(self._build(url, slug_title, slug_city, jp))
        return jobs

    def _build(self, url: str, slug_title: str, slug_city: str, jp: dict[str, Any] | None) -> RawJob:
        jp = jp or {}
        address: dict[str, Any] = {}
        loc = jp.get("jobLocation")
        if isinstance(loc, list) and loc:
            loc = loc[0]
        if isinstance(loc, dict):
            address = loc.get("address") or {}
        city = (address.get("addressLocality") or slug_city or "").strip()
        region = (address.get("addressRegion") or "").strip()
        description = "\n\n".join(strip_html(jp.get(k)) for k in ("description", "responsibilities", "qualifications") if jp.get(k))
        org = jp.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else None
        signals = [f"application deadline {str(jp.get('validThrough'))[:10]}"] if jp.get("validThrough") else []
        employment = jp.get("employmentType")
        if isinstance(employment, list):
            employment = ", ".join(employment)
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=DETAIL_RE.search(url).group(3),
            url_origin=url,
            job_title=(jp.get("title") or slug_title).strip(),
            company_name=f"{company or 'Randstad'} (agency, client not named)",
            location_raw=", ".join(x for x in (city, region, "Italy") if x),
            location_type="on-site",
            contract_type=employment,
            salary_raw=format_salary(jp.get("baseSalary")),
            description_raw=description or None,
            date_posted=parse_date(jp.get("datePosted")),
            metadata={"countries": ["it"], "cities": [city] if city else [], "signals": signals, "industry": jp.get("industry")},
        )
