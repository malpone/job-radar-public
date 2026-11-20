"""Jobicy: public JSON API (GET /api/v2/remote-jobs?count=&geo=europe&tag=). Remote jobs only.

The Europe filter is applied by the API. Jobicy asks users to credit the source: the link to the
original ad is always kept in the queue and in the spreadsheet.
"""

from __future__ import annotations

import logging
from typing import Any

from jobradar.common import as_list, parse_date, strip_html
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)


class JobicyScraper(BaseScraper):
    SOURCE_NAME = "jobicy"
    DEFAULT_DELAY = 3.0
    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        self.tags: list[str] = self.source_config().get("tags", [])

    def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for tag in self.tags:
            self._sleep_between_requests()
            try:
                data = self._request_with_retry(self.API_URL, params={"count": 50, "geo": "europe", "tag": tag}).json()
            except Exception as e:
                logger.warning("Jobicy: error on tag=%r: %s", tag, e)
                continue
            for row in data.get("jobs", []) or []:
                key = str(row.get("id") or row.get("url"))
                if key in seen:
                    continue
                seen.add(key)
                job = self.parse(row)
                if job:
                    jobs.append(job)
        return jobs

    def parse(self, row: dict[str, Any]) -> RawJob | None:
        title = (row.get("jobTitle") or "").strip()
        company = (row.get("companyName") or "").strip()
        url = (row.get("url") or "").strip()
        if not (title and company and url):
            return None
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=str(row.get("id") or ""),
            url_origin=url,
            job_title=title,
            company_name=company,
            location_raw=(row.get("jobGeo") or "Europe").strip(),
            location_type="remote",
            contract_type=", ".join(as_list(row.get("jobType"))) or None,
            seniority_level=row.get("jobLevel"),
            description_raw=strip_html(row.get("jobDescription")),
            tags=[t.lower() for t in as_list(row.get("jobIndustry"))],
            date_posted=parse_date(row.get("pubDate")),
            metadata={"geo_filter_api": "europe"},
        )
