"""In-memory deduplication (no database).

1. Same canonical URL (tracking parameters removed): same ad.
2. Same normalised company and similar title (Jaccard >= 0.65): same ad published on two sources.
   The source with the longest description wins.
3. Ads already present in the working spreadsheet (sheets "Jobs" and "Discarded"): skipped.
   Discards with reason "over the daily limit" or "to read" are retried, everything else is final.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from jobradar.common import canonical_url
from jobradar.etl.text import jaccard, normalize_company_name, title_tokens
from jobradar.models import RawJob

logger = logging.getLogger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.65
RETRY_REASONS = ("over the daily limit", "to read")


def company_key(name: str) -> str:
    base = (name or "").split("(")[0]
    return normalize_company_name(base) or base.lower().strip()


@dataclass
class ExistingIndex:
    urls: set[str] = field(default_factory=set)
    jobs: list[tuple[str, set[str]]] = field(default_factory=list)  # (company key, title tokens)


def _same_job(a_company: str, a_tokens: set[str], b_company: str, b_tokens: set[str]) -> bool:
    return bool(a_company) and a_company == b_company and jaccard(a_tokens, b_tokens) >= TITLE_SIMILARITY_THRESHOLD


def load_existing(xlsx: str | Path | None) -> ExistingIndex:
    """Index what the spreadsheet already knows (read-only)."""
    idx = ExistingIndex()
    if not xlsx or not Path(xlsx).exists():
        return idx
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        if "Jobs" in wb.sheetnames:
            for r in wb["Jobs"].iter_rows(min_row=5, values_only=True):
                if len(r) > 19 and r[19]:
                    idx.urls.add(canonical_url(str(r[19])))
                if len(r) > 3 and r[2] and r[3]:
                    idx.jobs.append((company_key(str(r[2])), title_tokens(str(r[3]))))
        if "Discarded" in wb.sheetnames:
            for r in wb["Discarded"].iter_rows(min_row=2, values_only=True):
                reason = str(r[4] or "") if len(r) > 4 else ""
                if any(k in reason for k in RETRY_REASONS):
                    continue
                if len(r) > 5 and r[5]:
                    idx.urls.add(canonical_url(str(r[5])))
    finally:
        wb.close()
    logger.info("Spreadsheet: %d known links, %d known ads", len(idx.urls), len(idx.jobs))
    return idx


def dedup_jobs(jobs: list[RawJob], existing: ExistingIndex) -> tuple[list[RawJob], list[tuple[RawJob, str]]]:
    """Return (new unique ads, dropped ads with the reason)."""
    kept: list[RawJob] = []
    dropped: list[tuple[RawJob, str]] = []
    seen_urls: dict[str, int] = {}
    keys: list[tuple[str, set[str]]] = []

    for job in jobs:
        url = canonical_url(job.url_origin)
        comp = company_key(job.company_name)
        tokens = title_tokens(job.job_title)

        if url in existing.urls:
            dropped.append((job, "already in the spreadsheet"))
            continue
        if any(_same_job(comp, tokens, c, t) for c, t in existing.jobs):
            dropped.append((job, "already in the spreadsheet (same company and title)"))
            continue

        dup_of = seen_urls.get(url)
        if dup_of is None:
            for i, (c, t) in enumerate(keys):
                if _same_job(comp, tokens, c, t):
                    dup_of = i
                    break
        if dup_of is not None:
            other = kept[dup_of]
            if len(job.description_raw or "") > len(other.description_raw or ""):
                kept[dup_of] = job
                dropped.append((other, f"duplicate of {job.source}"))
            else:
                dropped.append((job, f"duplicate of {other.source}"))
            continue

        seen_urls[url] = len(kept)
        keys.append((comp, tokens))
        kept.append(job)
    return kept, dropped
