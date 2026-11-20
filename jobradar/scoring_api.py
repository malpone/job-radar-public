"""Optional scoring path: call the Anthropic API instead of a Claude Code skill.

The contract is identical to the skill path: the model proposes partial scores as JSON, and
jobradar.rubric.validate() checks every number before anything reaches the spreadsheet.

    python -m jobradar.scoring_api --queue data/queue/queue-2026-01-01.json --limit 20 --out data/ev.json
    python -m jobradar.excel write --evaluations data/ev.json

Needs ANTHROPIC_API_KEY (see .env.example). The model is configurable; no key is ever stored in the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from jobradar.common import ROOT
from jobradar.rubric import load_rubric, validate

DEFAULT_MODEL = "claude-sonnet-5-5"
PROFILE_PATH = ROOT / "config" / "profile.yaml"
PROFILE_EXAMPLE = ROOT / "config" / "profile.example.yaml"

OUTPUT_KEYS = (
    "company, role, location_mode, track, salary, posted_applicants, skills, role_pts, location_pts, "
    "sector_pts, contract_pts, other_pts, cap, skills_fit, gaps, rationale, link, source"
)


def build_prompt(profile: dict[str, Any], rubric: dict[str, Any], ads: list[dict[str, Any]]) -> str:
    caps = "\n".join(f"- cap {cap}: " + " / ".join(reasons) for cap, reasons in sorted(rubric["caps"].items()))
    soft = "\n".join(f"- {r}" for r in rubric.get("soft_rules", []))
    crit = ", ".join(f"{k} max {v}" for k, v in rubric["criteria"].items())
    return f"""You are scoring job ads for one candidate. Be honest: do not inflate scores.

CANDIDATE PROFILE (YAML):
{yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)}

RUBRIC. Partial scores: {crit}. Raw score = sum. Final score = min(raw, cap) when a cap applies.
Bands: high >= {rubric['bands']['high']}, medium >= {rubric['bands']['medium']}, low below.
Caps (use only 40, 45 or 60, or null):
{caps}
Other rules:
{soft}

Evaluate each ad below using ONLY the text provided (do not invent requirements; if the text is short, say so
in the rationale). Answer with a JSON array, one object per ad, with exactly these keys:
{OUTPUT_KEYS}
Rules for the keys: "skills" is the technical-skills score (0-{rubric['criteria']['skills']}); "cap" is 40, 45, 60 or null;
"link" is the ad "url"; "source" is the ad "source"; "salary" is the stated salary or "not indicated";
"posted_applicants" is "<date>, applicants n/a"; "rationale" is at most {rubric['prompt_style']['rationale_max_sentences']} sentences and names the main limit.
Return only the JSON array.

ADS (JSON):
{json.dumps(ads, ensure_ascii=False, indent=1)}
"""


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull the first JSON array out of a model reply (tolerates code fences and chatter)."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in the model reply")
    data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("the model reply is not a JSON array")
    return data


def score_ads(ads: list[dict[str, Any]], profile: dict[str, Any], client: Any = None, model: str = DEFAULT_MODEL,
              batch_size: int = 10, max_tokens: int = 8000) -> tuple[list[dict[str, Any]], list[str]]:
    """Score ads in batches. Returns (valid evaluations, validation problems). `client` can be any object
    with an Anthropic-style `messages.create`; it is injected in tests."""
    rubric = load_rubric()
    if client is None:
        import anthropic  # imported lazily: the API path is optional

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    valid: list[dict[str, Any]] = []
    problems: list[str] = []
    for i in range(0, len(ads), batch_size):
        batch = ads[i:i + batch_size]
        reply = client.messages.create(model=model, max_tokens=max_tokens,
                                       messages=[{"role": "user", "content": build_prompt(profile, rubric, batch)}])
        text = "".join(getattr(b, "text", "") for b in reply.content)
        for ev in extract_json_array(text):
            try:
                validate(ev, rubric)
                valid.append(ev)
            except ValueError as e:  # a bad record is reported, never written
                problems.append(str(e))
    return valid, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score a queue through the Anthropic API")
    ap.add_argument("--queue", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model", default=os.environ.get("JOBRADAR_MODEL", DEFAULT_MODEL))
    args = ap.parse_args(argv)

    profile_file = PROFILE_PATH if PROFILE_PATH.exists() else PROFILE_EXAMPLE
    profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
    ads = json.loads(args.queue.read_text(encoding="utf-8"))["ads"][: args.limit]
    valid, problems = score_ads(ads, profile, model=args.model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(valid, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"scored {len(valid)} of {len(ads)} ads with {args.model}; profile: {profile_file.name}")
    for p in problems:
        print("  rejected:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
