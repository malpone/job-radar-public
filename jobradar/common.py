"""Shared helpers: config loading, honest HTTP headers, HTML cleaning, dates, URLs."""

from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "sources.yaml"
DEFAULT_UA = "JobRadar/2.0 (personal job search, low volume)"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read config/sources.yaml (or the given file)."""
    p = Path(path) if path else DEFAULT_CONFIG
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def honest_headers(user_agent: str | None = None, accept: str = "application/json") -> dict[str, str]:
    """Declare who we are: a plain User-Agent, no browser impersonation."""
    return {"User-Agent": user_agent or DEFAULT_UA, "Accept": accept, "Accept-Language": "en;q=0.9,it;q=0.8"}


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def strip_html(text: str | None) -> str:
    """HTML to plain text, keeping line breaks between blocks."""
    if not text:
        return ""
    t = html_lib.unescape(text)
    t = _BLOCK_RE.sub("\n", t)
    t = _TAG_RE.sub("", t)
    t = html_lib.unescape(t)
    t = _WS_RE.sub(" ", t)
    out: list[str] = []
    for ln in (ln.strip() for ln in t.splitlines()):
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def parse_date(value: Any) -> date | None:
    """ISO 8601, Unix timestamp or RFC 822 (RSS) to date."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})T", s)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        return None


_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}


def canonical_url(url: str) -> str:
    """URL without tracking parameters and fragment: the deduplication key."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def as_list(value: Any) -> list[str]:
    """Normalise fields that may arrive as list, string or None."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    s = str(value).strip()
    return [s] if s else []
