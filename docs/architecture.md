# Architecture

## Data flow

```
sources (API / RSS / HTML)  ┐
alert emails (.eml/.html)   ┴─> RawJob ─> age filter ─> title pre-filter ─> geo filter ─> dedup ─> queue JSON
                                                                                                      │
                                                  skill or API: partial scores + rationale ◄──────────┘
                                                                  │
                                              rubric.validate() ──┴─> excel.write() ─> spreadsheet (+ mirror, + CSV fallback)
```

## Modules

| Module | Responsibility |
|---|---|
| `jobradar/models.py` | `RawJob` dataclass, `BaseScraper` (retry with exponential backoff, polite pauses, `run()` that never raises) |
| `jobradar/common.py` | config loading, honest headers, HTML cleaning, date parsing, canonical URL |
| `jobradar/sources/*` | one scraper per source; each returns `RawJob` objects |
| `jobradar/email_alerts.py` | parser for job-alert emails; no network |
| `jobradar/etl/title_prefilter.py` | off-track title rules and lane priority |
| `jobradar/etl/geo_filter.py` | remote scope, on-site/hybrid radius, optional geocoder hook |
| `jobradar/etl/dedup.py` | same-URL and same-company-similar-title deduplication, spreadsheet-aware |
| `jobradar/collect.py` | the pipeline and the queue writer |
| `jobradar/rubric.py` | loads `rubric.yaml`; `validate`, `final_score`, `band` |
| `jobradar/scoring_api.py` | optional Anthropic API scoring, same contract as the skill |
| `jobradar/excel.py` | create, write, override, verify, mirror and fallbacks |
| `skill/SKILL.md` | the scoring routine for Claude Code |

## The queue record

Each ad in `queue-YYYY-MM-DD.json` has: `priority` (1-3), `local`, `source`, `id`, `url`, `title`, `company`, `location`, `mode`, `km_from_home`, `geo_reason`, `geo_notes`, `posted`, `salary_raw`, `contract`, `seniority`, `signals`, `description` (truncated to `description_max_chars`), `description_length`.

## The evaluation record (scoring output)

`company, role, location_mode, track, salary, posted_applicants, skills, role_pts, location_pts, sector_pts, contract_pts, other_pts, cap, skills_fit, gaps, rationale, link, source`

Partial scores live in `[0, max]` per `rubric.yaml`; `cap` is `null`, 40, 45 or 60; the final score is `min(sum of partials, cap)`.

## Optional geocoder

`GeoFilter(config, geocoder=obj)` accepts any object with `.geocode(query)` returning something with `latitude`, `longitude` and `country_iso2`. It is only used for cities missing from the gazetteer and only accepted for the configured local countries. Without it, an unknown city is discarded as "location cannot be determined".

## Adding a source

Subclass `BaseScraper`, set `SOURCE_NAME`, implement `fetch_jobs()` returning `RawJob`s, register it in `jobradar/sources/__init__.py`, add a block under `sources:` in `config/sources.yaml` (disabled until you have checked the terms of use), and add a parsing test with a small inline fixture.

## Changing the rules later

Edit `config/rubric.yaml`, then re-apply to existing rows with an override file:

```json
[{"link": "https://example.com/jobs/1", "set": {"cap": 40}, "append_gaps": "UK only.", "append_rationale": "Cap 40 (UK rule)."}]
```

```bash
python -m jobradar.excel write --override override.json
```

Rows are re-sorted and re-verified; user-written Status and Notes are kept.
