"""Collection pipeline: sources + email alerts -> filters -> dedup -> scoring queue.

Steps:
1. scrape the enabled sources (one failing source never stops the others) and read alert emails
2. drop ads older than max_age_days
3. title pre-filter
4. geographic filter (remote Europe, on-site and hybrid radius from home)
5. deduplicate across sources and against the spreadsheet
6. write data/queue/queue-YYYY-MM-DD.json and discards-YYYY-MM-DD.json

Scoring is NOT done here: the skill (skill/SKILL.md) or jobradar/scoring_api.py reads the queue.

    python -m jobradar.collect                      # full run
    python -m jobradar.collect --dry-run            # counts only, no files
    python -m jobradar.collect --sources himalayas,jobicy --no-email
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

try:  # optional: use the OS certificate store (corporate TLS inspection)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from jobradar.common import ROOT, canonical_url, load_config
from jobradar.email_alerts import load_alerts
from jobradar.etl.dedup import dedup_jobs, load_existing
from jobradar.etl.geo_filter import GeoDecision, GeoFilter
from jobradar.etl.title_prefilter import prefilter_title, title_priority
from jobradar.models import RawJob
from jobradar.sources import LABELS, REGISTRY

logger = logging.getLogger("jobradar.collect")


def to_queue_item(job: RawJob, geo: GeoDecision, max_chars: int) -> dict:
    md = job.metadata or {}
    desc = (job.description_raw or "").strip()
    return {
        "priority": title_priority(job.job_title),  # 1 = RevOps/SalesOps/GTM, 2 = CRM/MarketingOps/BI, 3 = rest
        "local": geo.mode in ("on-site", "hybrid") or bool(md.get("local_watch")),
        "source": LABELS.get(job.source, job.source),
        "source_id": job.source,
        "id": job.external_id or canonical_url(job.url_origin),
        "url": canonical_url(job.url_origin),
        "title": job.job_title,
        "company": job.company_name,
        "location": job.location_raw,
        "mode": geo.mode,
        "km_from_home": geo.km,
        "geo_reason": geo.reason,
        "geo_notes": geo.notes,
        "posted": job.date_posted.isoformat() if job.date_posted else None,
        "salary_raw": job.salary_raw,
        "contract": job.contract_type,
        "seniority": job.seniority_level,
        "signals": md.get("signals", []),
        "local_company": bool(md.get("local_watch")),
        "description": desc[:max_chars],
        "description_truncated": len(desc) > max_chars,
        "description_length": len(desc),
    }


def discard(job: RawJob, reason: str) -> dict:
    return {"source": LABELS.get(job.source, job.source), "id": job.external_id or canonical_url(job.url_origin),
            "company": job.company_name, "role": job.job_title, "reason": reason, "url": canonical_url(job.url_origin)}


def run(cfg: dict, only: set[str] | None = None, use_email: bool = True, geocoder=None) -> tuple[list[dict], list[dict], dict]:
    """Run the pipeline in memory. Returns (queue, discards, per-source report)."""
    sources = {n: c for n, c in REGISTRY.items()
               if (only is None or n in only) and cfg.get("sources", {}).get(n, {}).get("enabled", False)}
    geo = GeoFilter(cfg, geocoder=geocoder)

    all_jobs: list[RawJob] = []
    report: dict[str, dict] = {}
    for name, cls in sources.items():
        result = cls(config=cfg).run()
        all_jobs.extend(result.jobs)
        report[name] = {"collected": len(result.jobs), "status": result.status, "errors": result.errors[:2]}
    ea = cfg.get("email_alerts", {})
    if use_email and ea.get("enabled") and (only is None or "email" in only):
        directory = Path(ea.get("directory", "data/alerts"))
        alerts = load_alerts(directory if directory.is_absolute() else ROOT / directory)
        all_jobs.extend(alerts)
        report["email"] = {"collected": len(alerts), "status": "success" if alerts else "empty", "errors": []}

    cutoff = date.today() - timedelta(days=int(cfg.get("max_age_days", 30)))
    discards: list[dict] = []
    passed: list[tuple[RawJob, GeoDecision]] = []
    for job in all_jobs:
        if job.date_posted and job.date_posted < cutoff:
            discards.append(discard(job, f"older than {cfg.get('max_age_days', 30)} days"))
            continue
        pf = prefilter_title(job.job_title)
        if not pf.keep:
            discards.append(discard(job, pf.reason))
            continue
        if job.source == "email":  # alerts have no structured geography: the alert itself is the filter
            decision = GeoDecision(True, "from an alert email (location as written in the alert)", "remote", place=job.location_raw)
        else:
            decision = geo.decide(job)
        if not decision.keep:
            discards.append(discard(job, f"out of area: {decision.reason}"))
            continue
        passed.append((job, decision))

    excel_path = cfg.get("excel", {}).get("path")
    existing = load_existing(excel_path if excel_path and Path(excel_path).is_absolute() else (ROOT / excel_path if excel_path else None))
    unique, dups = dedup_jobs([j for j, _ in passed], existing)
    decisions = {id(j): d for j, d in passed}
    discards.extend(discard(j, why) for j, why in dups)

    max_chars = int(cfg.get("description_max_chars", 4000))
    queue = [to_queue_item(j, decisions[id(j)], max_chars) for j in unique]
    queue.sort(key=lambda x: x["posted"] or "", reverse=True)            # most recent first...
    queue.sort(key=lambda x: (not x["local"], x["priority"]))            # ...within local-first, then lane (stable sort)
    return queue, discards, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Job Radar: collect, filter, deduplicate, build the scoring queue")
    ap.add_argument("--sources", default=None, help="comma-separated sources (default: all enabled; 'email' for alerts)")
    ap.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    ap.add_argument("--no-email", action="store_true", help="skip the alert emails")
    ap.add_argument("--config", default=None, help="path to sources.yaml")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    only = {s.strip() for s in args.sources.split(",")} if args.sources else None
    queue, discards, report = run(cfg, only, use_email=not args.no_email)

    per_source = Counter(x["source"] for x in queue)
    print("\nJOB RADAR SUMMARY")
    for name, r in report.items():
        label = LABELS.get(name, name)
        print(f"  {label:18} collected={r['collected']:4}  in queue={per_source.get(label, 0):3}  status={r['status']}")
    reasons = Counter(d["reason"].split(" (")[0].split(":")[0] for d in discards)
    print(f"  Discarded: {len(discards)}  " + ", ".join(f"{k}={v}" for k, v in reasons.most_common(6)))
    print(f"  QUEUE: {len(queue)}  (local {sum(x['local'] for x in queue)}, "
          + ", ".join(f"lane {p}={sum(1 for x in queue if x['priority'] == p)}" for p in (1, 2, 3)) + ")")
    if args.dry_run:
        for x in queue[:15]:
            print(f"   - [{x['source']}] {x['title'][:55]} @ {x['company'][:28]} | {x['geo_reason']}")
        return 0

    out_dir = Path(cfg.get("queue_dir", "data/queue"))
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    meta = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "sources": report,
            "rules": f"remote Europe; on-site within {cfg['geo']['on_site_max_km']} km; hybrid within {cfg['geo']['hybrid_max_km']} km"}
    (out_dir / f"queue-{stamp}.json").write_text(json.dumps({"meta": meta, "ads": queue}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"discards-{stamp}.json").write_text(json.dumps({"meta": meta, "discards": discards}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  Queue: {out_dir / f'queue-{stamp}.json'}")
    failed = [n for n, r in report.items() if r["status"] == "failed"]
    return 2 if report and len(failed) == len(report) else 0


if __name__ == "__main__":
    sys.exit(main())
