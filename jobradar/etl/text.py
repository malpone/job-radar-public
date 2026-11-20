"""Small text utilities for matching companies and titles."""

from __future__ import annotations

import math
import re

LEGAL_SUFFIXES = sorted(
    [
        "s.p.a.", "spa", "s.p.a", "s.r.l.", "srl", "s.r.l", "s.r.l.s.", "srls", "s.n.c.", "snc", "s.a.s.", "sas",
        "ltd", "limited", "llc", "l.l.c.", "inc.", "inc", "incorporated", "corp.", "corp", "corporation",
        "co.", "co", "plc", "gmbh", "ag", "kg", "ohg", "sa", "s.a.", "bv", "b.v.", "nv", "n.v.", "ab", "oy", "as", "a/s",
    ],
    key=len,
    reverse=True,
)

_STOPWORDS = {
    "a", "an", "the", "di", "del", "della", "dei", "degli", "delle", "il", "lo", "la", "i", "gli", "le", "un", "una",
    "for", "in", "at", "on", "with", "and", "or", "to", "per", "con", "su", "da", "junior", "senior", "mid", "lead",
}


def normalize_company_name(name: str) -> str:
    """'Acme Inc.' -> 'acme'; strips stacked legal suffixes and punctuation."""
    if not name:
        return ""
    normalized = name.lower().strip()
    for _ in range(3):
        changed = False
        for suffix in LEGAL_SUFFIXES:
            new = re.sub(rf"[\s,]*\b{re.escape(suffix)}\.?\s*$", "", normalized, flags=re.IGNORECASE)
            if new != normalized:
                normalized = new.strip()
                changed = True
                break
        if not changed:
            break
    normalized = re.sub(r"[.,;:]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def title_tokens(title: str) -> set[str]:
    """Meaningful lowercase tokens of a title, for Jaccard matching."""
    if not title:
        return set()
    tokens = re.findall(r"\b[a-zA-Zàèéìòù]{2,}\b", title.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h)), 2)
