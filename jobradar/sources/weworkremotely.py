"""We Work Remotely: public RSS feeds per category. Remote jobs only.

Each <item> has title ("Company: Role"), region, country, type, description (escaped HTML), pubDate, link.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any

from jobradar.common import parse_date, strip_html
from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(item: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
    if not m:
        return ""
    val = m.group(1).strip()
    if val.startswith("<![CDATA["):
        val = val[9:-3]
    return html_lib.unescape(val).strip()


class WeWorkRemotelyScraper(BaseScraper):
    SOURCE_NAME = "weworkremotely"
    DEFAULT_DELAY = 3.0
    ACCEPT = "application/rss+xml"
    FEED_URL = "https://weworkremotely.com/categories/{category}.rss"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        self.categories: list[str] = self.source_config().get("categories", [])

    def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for cat in self.categories:
            self._sleep_between_requests()
            try:
                xml = self._request_with_retry(self.FEED_URL.format(category=cat)).text
            except Exception as e:
                logger.warning("WWR: error on category %s: %s", cat, e)
                continue
            for item in _ITEM_RE.findall(xml):
                job = self.parse_item(item)
                if job and job.url_origin not in seen:
                    seen.add(job.url_origin)
                    jobs.append(job)
        return jobs

    def parse_item(self, item: str) -> RawJob | None:
        full_title = _tag(item, "title")
        link = _tag(item, "link") or _tag(item, "guid")
        if not (full_title and link):
            return None
        company, _, title = full_title.partition(": ")
        if not title:
            company, title = "n/a", full_title
        region, country = _tag(item, "region"), _tag(item, "country")
        return RawJob(
            source=self.SOURCE_NAME,
            external_id=link.rstrip("/").rsplit("/", 1)[-1],
            url_origin=link,
            job_title=title.strip(),
            company_name=company.strip(),
            location_raw=", ".join(x for x in (region, country) if x) or None,
            location_type="remote",
            contract_type=_tag(item, "type") or None,
            description_raw=strip_html(_tag(item, "description")),
            date_posted=parse_date(_tag(item, "pubDate")),
            metadata={"region": region, "country": country, "category": _tag(item, "category")},
        )
