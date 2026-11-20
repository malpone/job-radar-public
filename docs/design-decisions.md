# Design decisions

## 1. The model judges, the code validates

A rubric score is a judgment, so a model produces it. But a model can return a number outside its range, an unknown cap or a broken link. `jobradar/rubric.py` rejects those records before any file is touched. Both scoring paths (skill and API) share the same validator, so swapping the model changes nothing downstream.

## 2. Caps, not subtraction

Early on, "UK only" meant "location points at most 8" and "language the candidate does not speak" meant a few lost points. Both left clearly unreachable ads in the medium band: the best-scored ad of one run (67) was UK-only.

Subtracting a fixed amount (20? 30?) is worse than it looks: a 65 and a 55 with the same blocker end up 20 points apart for a reason that has nothing to do with the blocker. A cap says what is actually true: "whatever the rest is, this ad cannot be applied to". The cap values are few (40, 45, 60), explicit, and listed in `config/rubric.yaml`. 40 was chosen so that a capped ad stays visible in the spreadsheet but can never reach the medium band.

## 3. One rubric file

`config/rubric.yaml` feeds the LLM prompt, the validator, the spreadsheet's Criteria sheet and the conditional-formatting thresholds. A rule changed in one place changes everywhere, and `--override` re-applies it to rows already scored.

## 4. LinkedIn only from the user's own alert emails

Reading LinkedIn pages automatically conflicts with its terms of use, even without logging in. The only LinkedIn input is the alert email the user already receives, parsed locally. Such ads carry only id, title, company and place, so they are scored with lower confidence and the rationale says so. This keeps the project publishable and the data honest.

## 5. Isolation and graceful degradation

Every source runs in its own `try`: a failing source is reported in the summary and the run goes on. Writing the spreadsheet retries when the file is locked, then writes the mirror and warns that the two copies differ, then writes a CSV. A run that does less than expected says so instead of failing silently.

## 6. Honest scoring beats a flattering shortlist

On 180 scored ads from real runs, none reached the high band (70). The best ones were 64-67. A scoring system that never says "no" is useless; this one separates "worth reading" from "apply tonight" and keeps the second list short.

## 7. What I would do next

- A feedback loop: use the Status column (applied, interview, rejected) to check which partial scores predict outcomes.
- Replace the great-circle distance with travel time.
- Store the history in SQLite instead of a spreadsheet when more than one user needs it.
