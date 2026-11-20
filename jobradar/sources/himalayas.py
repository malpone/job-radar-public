"""Himalayas: public JSON API (GET /jobs/api/search?q=...). Remote jobs only.

Fields: title, companyName, description (HTML), locationRestrictions (country list, empty = worldwide),
timezoneRestrictions, minSalary, maxSalary, currency, salaryPeriod, pubDate (Unix), applicationLink, guid.
"""

from __future__ import annotations

import logging
from typing import Any

from jobradar.common import as_list, parse_date, strip_html
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)


class HimalayasScraper(BaseScraper):
    SOURCE_NAME = "himalayas"
    DEFAULT_DELAY = 2.0
    SEARCH_URL = "https://himalayas.app/jobs/api/search"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        self.queries: list[str] = self.config.get("queries", [])

    def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for q in self.queries:
            self._sleep_between_requests()
            try:
                data = self._request_with_retry(self.SEARCH_URL, params={"q": q}).json()
            except Exception as e:
                logger.warning("Himalayas: error on q=%r: %s", q, e)
                continue
            for row in data.get("jobs", []) or []:
                key = row.get("guid") or row.get("applicationLink")
                if not key or key in seen:
                    continue
                seen.add(key)
                job = self.parse(row)
                if job:
                    jobs.append(job)
        return jobs

    def parse(self, row: dict[str, Any]) -> RawJob | None:
        title = (row.get("title") or "").strip()
        company = (row.get("companyName") or "").strip()
        url = (row.get("guid") or row.get("applicationLink") or "").strip()
        if not (title and company and url):
            return None
        restrictions = as_list(row.get("locationRestrictions"))
        salary = None
        if row.get("minSalary"):
            salary = f"{row.get('minSalary')}-{row.get('maxSalary') or ''} {row.get('currency') or ''} {row.get('salaryPeriod') or ''}".strip()
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=url.rstrip("/").rsplit("/", 1)[-1],
            url_origin=url,
            job_title=title,
            company_name=company,
            location_raw=", ".join(restrictions) if restrictions else "Worldwide",
            location_type="remote",
            contract_type=row.get("employmentType"),
            salary_raw=salary,
            seniority_level=", ".join(as_list(row.get("seniority"))) or None,
            description_raw=strip_html(row.get("description")),
            tags=[c.lower() for c in as_list(row.get("categories"))][:15],
            date_posted=parse_date(row.get("pubDate")),
            metadata={"countries_named": [r.lower() for r in restrictions], "timezones": row.get("timezoneRestrictions")},
        )
