"""Working Nomads: public JSON endpoint (GET /api/exposed_jobs/), one call, recent ads. Remote only.

Fields: url, title, description (HTML), company_name, category_name, tags (comma separated string),
location, pub_date.
"""

from __future__ import annotations

import logging
from typing import Any

from jobradar.common import parse_date, strip_html
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)


class WorkingNomadsScraper(BaseScraper):
    SOURCE_NAME = "workingnomads"
    DEFAULT_DELAY = 2.0
    API_URL = "https://www.workingnomads.com/api/exposed_jobs/"

    def fetch_jobs(self) -> list[RawJob]:
        self._sleep_between_requests()
        rows = self._request_with_retry(self.API_URL).json() or []
        return [j for j in (self.parse(r) for r in rows) if j]

    def parse(self, row: dict[str, Any]) -> RawJob | None:
        title = (row.get("title") or "").strip()
        company = (row.get("company_name") or "").strip()
        url = (row.get("url") or "").strip()
        if not (title and company and url):
            return None
        tags = [t.strip().lower() for t in str(row.get("tags") or "").split(",") if t.strip()]
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=url.rstrip("/").rsplit("/", 1)[-1],
            url_origin=url,
            job_title=title,
            company_name=company,
            location_raw=(row.get("location") or "").strip() or None,
            location_type="remote",
            description_raw=strip_html(row.get("description")),
            tags=tags,
            date_posted=parse_date(row.get("pub_date")),
            metadata={"category": row.get("category_name")},
        )
