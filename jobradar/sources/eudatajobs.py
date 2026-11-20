"""EU Data Jobs: HTML cards, one <div class="job"> per ad. Disabled by default: verify the site terms.

Each card has: <a href> (company career page), <h2> title, <h3> company then the map-marker icon and
"Country (Mode)", and a <div> with the calendar icon and the date ("Fri Oct 16 2026").
The page has no description: scoring uses title, company and place and says so in the rationale.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from jobradar.models import BaseScraper, RawJob

logger = logging.getLogger(__name__)

MODE_RE = re.compile(r"\((on-site|onsite|remote|hybrid)\)\s*$", re.I)
_MODE_MAP = {"on-site": "on-site", "onsite": "on-site", "remote": "remote", "hybrid": "hybrid"}


def parse_card(card: Tag) -> RawJob | None:
    a, h2, h3 = card.find("a", href=True), card.find("h2"), card.find("h3")
    if not (a and h2 and h3):
        return None
    title = h2.get_text(" ", strip=True)
    company_parts: list[str] = []
    location_parts: list[str] = []
    after_marker = False
    for node in h3.children:
        if isinstance(node, Tag) and node.name == "i" and any("map-marker" in c for c in node.get("class", [])):
            after_marker = True
            continue
        text = node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node) if isinstance(node, NavigableString) else ""
        text = text.replace("\xa0", " ").strip()
        if text:
            (location_parts if after_marker else company_parts).append(text)
    company, location = " ".join(company_parts).strip(), " ".join(location_parts).strip()
    mode = None
    m = MODE_RE.search(location)
    if m:
        mode = _MODE_MAP[m.group(1).lower()]
        location = location[: m.start()].strip()

    posted = None
    cal = card.find("i", class_=lambda c: c and "fa-calendar-alt" in c)
    if cal and cal.parent:
        try:
            posted = datetime.strptime(cal.parent.get_text(" ", strip=True).title(), "%a %b %d %Y").date()
        except ValueError:
            posted = None

    url = a["href"].strip()
    if not (title and company and url.startswith("http")):
        return None
    return RawJob(
        source="eudatajobs", external_id=url, url_origin=url, job_title=title, company_name=company,
        location_raw=location or None, location_type=mode, date_posted=posted,
        metadata={"signals": ["description not available on the source: score on title, company and place"]},
    )


class EUDataJobsScraper(BaseScraper):
    SOURCE_NAME = "eudatajobs"
    ACCEPT = "text/html"
    DEFAULT_DELAY = 3.0
    URL = "https://eudatajobs.com/search"

    def fetch_jobs(self) -> list[RawJob]:
        self._sleep_between_requests()
        soup = BeautifulSoup(self._request_with_retry(self.URL).text, "lxml")
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for card in soup.select("div.job"):
            job = parse_card(card)
            if job and job.url_origin not in seen:
                seen.add(job.url_origin)
                jobs.append(job)
        return jobs
