"""freehire.me: JSON search API that aggregates ads from many applicant-tracking platforms.

Disabled by default in config/sources.yaml: check the service terms before enabling it.
Three kinds of search: RevOps keywords for remote work, the same keywords per country, and
optional company career pages ("local_companies") read with every title.

Fields: title, company, url, location, description (HTML), countries, regions, cities, work_mode,
posted_at, reality (stale/reposted signal). Best-effort service: if it does not answer, the source is skipped.
"""

from __future__ import annotations

import logging
from typing import Any

from jobradar.common import as_list, parse_date, strip_html
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)

_MODE_MAP = {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site", "on_site": "on-site", "on-site": "on-site", "office": "on-site"}


class FreehireScraper(BaseScraper):
    SOURCE_NAME = "freehire"
    DEFAULT_DELAY = 1.5

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        src = self.source_config()
        self.base_url = src.get("base_url", "https://freehire.me/api/v1/agent/jobs/search")
        self.page_size = int(src.get("page_size", 50))
        self.local_companies: list[str] = src.get("local_companies", [])
        self.country = str(self.config.get("geo", {}).get("local_countries", ["it"])[0]).upper()
        self.queries: list[str] = self.config.get("queries", [])
        self.max_age_days = int(self.config.get("max_age_days", 30))

    def fetch_jobs(self) -> list[RawJob]:
        seen: set[str] = set()
        jobs: list[RawJob] = []
        searches: list[tuple[str, list[tuple[str, str]], str | None]] = []
        for q in self.queries:
            searches.append((q, [("work_mode", "remote")], None))
            searches.append((q, [("countries", self.country)], None))
        for company in self.local_companies:
            searches.append((company, [("countries", self.country)], company))

        for query, extra, company_filter in searches:
            for page in range(2 if company_filter is None and extra[0][0] == "work_mode" else 1):
                params = [("q", query), ("limit", str(self.page_size)), ("offset", str(page * self.page_size)),
                          ("posted_within_days", str(self.max_age_days)), *extra]
                self._sleep_between_requests()
                try:
                    rows = self._request_with_retry(self.base_url, params=params).json().get("data") or []
                except Exception as e:  # one failed search does not stop the others
                    logger.warning("freehire: error on q=%r %s: %s", query, extra, e)
                    break
                for row in rows:
                    if company_filter and company_filter.split()[0].lower() not in str(row.get("company") or "").lower():
                        continue
                    key = str(row.get("public_slug") or row.get("url"))
                    if key in seen:
                        continue
                    seen.add(key)
                    job = self.parse(row, local_watch=bool(company_filter))
                    if job:
                        jobs.append(job)
                if len(rows) < self.page_size:
                    break
        return jobs

    def parse(self, row: dict[str, Any], local_watch: bool = False) -> RawJob | None:
        title = (row.get("title") or "").strip()
        company = (row.get("company") or "").strip()
        url = (row.get("url") or "").strip()
        if not (title and company and url):
            return None
        reality = row.get("reality") or {}
        signals: list[str] = []
        if isinstance(reality, dict):
            if reality.get("class") and reality.get("class") != "fresh":
                signals.append(f"freehire: ad flagged {reality.get('class')}")
            if reality.get("repost_count"):
                signals.append(f"reposted {reality.get('repost_count')} times")
        enrichment = row.get("enrichment") or {}
        salary = None
        if isinstance(enrichment, dict) and enrichment.get("salary_min"):
            salary = (f"{enrichment.get('salary_min')}-{enrichment.get('salary_max') or ''} "
                      f"{enrichment.get('salary_currency') or ''} per {enrichment.get('salary_period') or 'period n/a'}").strip()
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=str(row.get("public_slug") or ""),
            url_origin=url,
            job_title=title,
            company_name=company,
            location_raw=(row.get("location") or "").strip() or None,
            location_type=_MODE_MAP.get(str(row.get("work_mode") or "").lower()),
            salary_raw=salary,
            description_raw=strip_html(row.get("description")),
            tags=[t.lower() for t in as_list(row.get("skills"))][:20],
            date_posted=parse_date(row.get("posted_at")),
            metadata={
                "countries": [c.lower() for c in as_list(row.get("countries"))],
                "regions": [r.lower() for r in as_list(row.get("regions"))],
                "cities": as_list(row.get("cities")),
                "origin": row.get("source"),
                "signals": signals,
                "local_watch": local_watch,
            },
        )
