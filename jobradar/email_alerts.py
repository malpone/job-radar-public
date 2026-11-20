"""LinkedIn job-alert emails -> queue records. No network access, no LinkedIn pages.

The only LinkedIn input of Job Radar is the alert email the user already receives. Export those emails
as .eml (or save the HTML body as .html) into the alerts directory; this module extracts, for each ad,
the numeric id (from the .../jobs/view/<id> link), title, company and place, and builds the clean URL.
Nothing is downloaded: the ad is scored on title, company and place and the rationale says so.

The parser is heuristic: alert layouts change over time. It is covered by a synthetic fixture
(examples/alert_sample.html) shaped like a typical alert; adapt `_LABELS` if the layout drifts.
"""

from __future__ import annotations

import email
import logging
import re
from datetime import date
from email import policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from jobradar.models import RawJob

logger = logging.getLogger(__name__)

JOB_LINK_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\"']*-)?(\d{6,})", re.I)
SKIP_SUBJECT = re.compile(r"has been created|alert (was )?created", re.I)
# Text lines that are labels or buttons, not company or place.
_LABELS = re.compile(
    r"^(easy apply|actively recruiting|actively hiring|be an early applicant|promoted|new|view job|view jobs|apply|"
    r"apply now|see all jobs|unsubscribe|see more jobs|\d+\s+(connections?|school alumni|applicants?)\b.*|"
    r"\d+\s+(days?|hours?|weeks?)\s+ago|[\$€£]\s?[\d.,kK\s-]+.*)$",
    re.I,
)


class _Collector(HTMLParser):
    """Flattens HTML into a stream of ("link", url, text) and ("text", text) events."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._buf = []
        elif tag in ("br", "p", "div", "tr", "td", "li") and self._href is None:
            self.events.append(("break",))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href is not None:
            self.events.append(("link", self._href, " ".join(" ".join(self._buf).split())))
            self._href, self._buf = None, []

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._href is not None:
            self._buf.append(text)
        else:
            self.events.append(("text", text))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ·-|")


def parse_alert_html(html: str, posted: date | None = None) -> list[RawJob]:
    """Extract the ads of one alert email body."""
    col = _Collector()
    col.feed(html)
    jobs: list[RawJob] = []
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current and current["title"]:
            lines = [l for l in current["lines"] if l and not _LABELS.match(l)]
            company = lines[0] if lines else ""
            location = lines[1] if len(lines) > 1 else ""
            if company and not location:  # "Company . City" on one line
                for sep in ("·", " - ", " | "):
                    if sep in company:
                        company, location = (p.strip() for p in company.split(sep, 1))
                        break
            jobs.append(RawJob(
                source="email",
                external_id=current["id"],
                url_origin=f"https://www.linkedin.com/jobs/view/{current['id']}/",
                job_title=current["title"],
                company_name=_clean(company) or "n/a",
                location_raw=_clean(location) or None,
                date_posted=posted,
                metadata={"signals": ["from an alert email: scored on title, company and place only"]},
            ))
        current = None

    seen: set[str] = set()
    for ev in col.events:
        if ev[0] == "link":
            m = JOB_LINK_RE.search(ev[1])
            if not m:
                continue
            job_id, text = m.group(1), _clean(ev[2])
            if current and current["id"] == job_id:
                if not current["title"] and text and not _LABELS.match(text):
                    current["title"] = text
                continue
            if job_id in seen:
                continue
            close()
            seen.add(job_id)
            current = {"id": job_id, "title": text if text and not _LABELS.match(text) else "", "lines": []}
        elif ev[0] == "text" and current is not None:
            current["lines"].append(_clean(ev[1]))
    close()
    return jobs


def parse_email_file(path: Path) -> list[RawJob]:
    """Read one .eml or .html file."""
    if path.suffix.lower() == ".eml":
        msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        if SKIP_SUBJECT.search(str(msg.get("subject") or "")):
            return []
        posted = None
        try:
            posted = parsedate_to_datetime(str(msg.get("date"))).date()
        except (TypeError, ValueError):
            pass
        body = msg.get_body(preferencelist=("html",))
        html = body.get_content() if body else ""
        return parse_alert_html(html, posted)
    return parse_alert_html(path.read_text(encoding="utf-8", errors="replace"), date.fromtimestamp(path.stat().st_mtime))


def load_alerts(directory: str | Path) -> list[RawJob]:
    """Parse every .eml/.html file of the directory; ads are deduplicated by LinkedIn id."""
    d = Path(directory)
    if not d.exists():
        return []
    jobs: dict[str, RawJob] = {}
    for p in sorted(list(d.glob("*.eml")) + list(d.glob("*.html"))):
        try:
            for j in parse_email_file(p):
                jobs.setdefault(j.external_id or j.url_origin, j)
        except Exception as e:  # one unreadable file must not stop the rest
            logger.warning("alert file not read %s: %s", p.name, e)
    logger.info("Email alerts: %d distinct ads from %s", len(jobs), d)
    return list(jobs.values())
