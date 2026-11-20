---
name: job-radar-scoring
description: Score the daily Job Radar queue with the fixed rubric and write the spreadsheet. Use after `python -m jobradar.collect`, when data/queue/queue-YYYY-MM-DD.json exists.
---

You score job ads for one candidate. The pipeline already collected, filtered and deduplicated them; your job is judgment, not code. A deterministic script checks every number you produce.

## Inputs
- `data/queue/queue-YYYY-MM-DD.json` (today): ads already ordered by priority (local first, then lane 1 RevOps/Sales Ops/GTM, then lane 2 CRM/Marketing Ops/BI, then the rest; most recent first).
- `config/profile.yaml` (or `config/profile.example.yaml` if the real one does not exist): who is searching.
- `config/rubric.yaml`: criteria, maxima, bands and caps. It is the single source of truth, read it every run.

## Steps
1. If today's queue does not exist, run `python -m jobradar.collect` and read its summary. If it fails, say so and stop: never invent ads. If one source is "failed", report it and go on.
2. Take the ads in queue order and score **up to N new ads** (default 50; the target is "new ads if they exist": if the queue holds fewer, score them all and say so, do not pad with already-scored ads and do not lower the discard criteria). Read up to about 1.8 x N ads in total: ads that cannot be scored go to the discards file with a reason and do not count in N.
3. Read each ad from the `description` field only. Do not open web pages and never open LinkedIn. If the description is empty or under 300 characters (always the case for email alerts and EU Data Jobs) say so in the rationale and lower confidence. Do not invent requirements: if the text does not list them, write that.
4. Score with the rubric: six partial scores (skills, role, location, sector, contract, other) within their maxima. Raw score = sum. Apply a cap only when it is justified by the rubric (40, 45 or 60); final = min(raw, cap).
   - Cap 40 (always the low band): a required working language the candidate does not speak, or an ad written in such a language (also location points at most 3); remote or on-site only in the UK; Ireland only (also location points 0); remote reserved to US residents.
   - Language that is only a plus: subtract 5 from location points, no cap. No explicit requirement and an ad in English or Italian: no penalty, mention the risk in the gaps.
   - Cap 45: data engineering or AI role with SQL/warehouse or RAG missing entirely; software engineering role with the core stack missing entirely. Cap 60: a role built on one tool never used.
   - Salary not written: write "not indicated" and give half of the contract points.
   - Be honest. Partial scores are judgments; dates and requirements come from the text you read.
5. Write `data/queue/evaluations-YYYY-MM-DD.json`: a JSON list, one object per scored ad, with exactly these keys:
   `company, role, location_mode, track, salary, posted_applicants, skills, role_pts, location_pts, sector_pts, contract_pts, other_pts, cap, skills_fit, gaps, rationale, link, source`
   (`link` = the ad `url`, `source` = the ad `source`, `posted_applicants` = "<date>, applicants n/a", `rationale` = 1-3 sentences naming the main limit).
   If there are discards, write `data/queue/discards-claude-YYYY-MM-DD.json`: a list of `{company, role, reason, link, source, id}`.
6. Run `python -m jobradar.excel write --evaluations <file> [--discards <file>]`. Read the JSON summary. If it reports a validation error (a number outside its maximum, a cap that is not allowed, an invalid link) fix the JSON and run again. If `verification` is not "ok" or `identical_copies` is false, say so prominently.
7. Final answer, short: ads collected per source and in queue, failing sources, how many scored and discarded (with one or two examples), how many remain for tomorrow. A table of the new ads in score order (band as text: High, Medium, Low; company, role, place and mode, source, score, main limit, link). Put any High (score >= 70) first, with the reason.

## Rules of the house
- The queue is never edited. Ads you do not score today are not written anywhere: they come back tomorrow while the source still lists them.
- When a rule changes (a new cap, say), apply it to the rows already in the spreadsheet with `--override` (see `jobradar/excel.py`), never by hand.
- Never write personal data from the profile into the rationale beyond what the scoring needs.
