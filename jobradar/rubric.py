"""Deterministic side of scoring: the rubric, the validator and the final-score maths.

The LLM (skill or API) proposes partial scores. This module is what makes them trustworthy:
every number is checked against config/rubric.yaml before anything reaches the spreadsheet.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from jobradar.common import ROOT

RUBRIC_PATH = ROOT / "config" / "rubric.yaml"

# Keys of an evaluation record, in the order of the partial-score columns (I..N).
PARTIAL_KEYS = ["skills", "role_pts", "location_pts", "sector_pts", "contract_pts", "other_pts"]
CRITERIA_KEYS = ["skills", "role", "location", "sector", "contract", "other"]
TEXT_KEYS = ["company", "role", "location_mode", "track", "salary", "posted_applicants",
             "skills_fit", "gaps", "rationale", "link", "source"]


@lru_cache(maxsize=4)
def load_rubric(path: str | None = None) -> dict[str, Any]:
    with Path(path or RUBRIC_PATH).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def maxima(rubric: dict[str, Any] | None = None) -> dict[str, int]:
    """Map evaluation key -> maximum points."""
    rb = rubric or load_rubric()
    return {pk: int(rb["criteria"][ck]) for pk, ck in zip(PARTIAL_KEYS, CRITERIA_KEYS)}


def raw_score(ev: dict[str, Any]) -> float:
    return sum(float(ev.get(k) or 0) for k in PARTIAL_KEYS)


def final_score(ev: dict[str, Any]) -> float:
    """min(raw score, cap). The cap is optional."""
    raw = raw_score(ev)
    cap = ev.get("cap")
    return min(raw, float(cap)) if cap not in (None, "") else raw


def band(score: float, rubric: dict[str, Any] | None = None) -> str:
    rb = rubric or load_rubric()
    if score >= rb["bands"]["high"]:
        return "High"
    if score >= rb["bands"]["medium"]:
        return "Medium"
    return "Low"


def validate(ev: dict[str, Any], rubric: dict[str, Any] | None = None) -> None:
    """Raise ValueError if an evaluation breaks the rubric."""
    rb = rubric or load_rubric()
    label = f"{ev.get('company')} / {ev.get('role')}"
    for key, mx in maxima(rb).items():
        val = ev.get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not 0 <= val <= mx:
            raise ValueError(f"{label}: {key}={val!r} outside 0-{mx}")
    allowed = set(rb["allowed_caps"])
    if ev.get("cap") not in (None, "") and ev.get("cap") not in allowed:
        raise ValueError(f"{label}: cap {ev.get('cap')!r} not allowed (use one of {sorted(allowed)} or null)")
    if not str(ev.get("link") or "").startswith("https://"):
        raise ValueError(f"{label}: invalid link {ev.get('link')!r}")
    for key in ("company", "role", "rationale"):
        if not str(ev.get(key) or "").strip():
            raise ValueError(f"{label}: missing {key}")
