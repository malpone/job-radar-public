"""The scoring spreadsheet: everything mechanical, always identical.

    python -m jobradar.excel create
    python -m jobradar.excel write --evaluations ev.json [--discards d.json] [--override o.json]

The LLM only prepares JSON; this module validates it against config/rubric.yaml, adds the rows,
re-sorts everything by final score (keeping the Status and Notes columns written by the user),
rebuilds formulas, styles, conditional formatting, filter and status drop-down, saves the file,
optionally mirrors it to a second location and compares hashes. If the main file is locked (open in
Excel) it retries, then falls back to the mirror, then to an emergency CSV.

Evaluation JSON keys: company, role, location_mode, track, salary, posted_applicants,
skills, role_pts, location_pts, sector_pts, contract_pts, other_pts, cap, skills_fit, gaps,
rationale, link, source.
Discards JSON keys: company, role, reason, link, source, id (optional).
Override JSON: list of {"link": ..., "set": {key: value}, "append_gaps": "...", "append_rationale": "..."}
applied to rows already in the spreadsheet (used when a rule changes after ads were scored).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from jobradar.common import load_config
from jobradar.rubric import PARTIAL_KEYS, band, final_score, load_rubric, maxima, validate

SHEET, SHEET_CRITERIA, SHEET_DISCARDED = "Jobs", "Criteria", "Discarded"
STATUSES = ["To review", "Applied", "Interview", "Rejected", "Closed"]
FIRST_ROW = 5
LAST_COL = "W"
COLUMNS = [  # (header, width)
    ("Band", 9), ("Final score (0-100)", 10), ("Company", 22), ("Role", 32), ("Location & mode", 28),
    ("Track", 16), ("Salary", 18), ("Posted & applicants", 20), ("Skills (max 30)", 9), ("Role (max 25)", 9),
    ("Location (max 15)", 9), ("Sector (max 10)", 9), ("Contract (max 10)", 9), ("Other (max 10)", 9),
    ("Raw score", 8), ("Cap", 8), ("Skills in fit", 36), ("Gaps and missing requirements", 36),
    ("Rationale", 48), ("Link", 36), ("Status", 14), ("Notes", 28), ("Source", 16),
]
DISCARDED_HEADER = ["Date", "ID", "Company", "Role", "Reason", "Link", "Source"]

THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BASE_FONT = Font(name="Arial", size=10)
BOLD_FONT = Font(name="Arial", size=10, bold=True)
BLUE_FONT = Font(name="Arial", size=10, color="FF0000FF")
LINK_FONT = Font(name="Arial", size=10, color="FF0563C1", underline="single")
CENTER = Alignment(wrap_text=True, vertical="top", horizontal="center")
LEFT = Alignment(wrap_text=True, vertical="top")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="FF1F3864")
HEADER_ALIGN = Alignment(wrap_text=True, vertical="center", horizontal="center")

# Spreadsheet column letter -> evaluation key
TEXT_COLS = {"C": "company", "D": "role", "E": "location_mode", "F": "track", "G": "salary", "H": "posted_applicants",
             "Q": "skills_fit", "R": "gaps", "S": "rationale", "U": "status", "V": "notes", "W": "source"}
PARTIAL_COLS = dict(zip(PARTIAL_KEYS, "IJKLMN"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def resolve_paths(config_path: str | None = None) -> tuple[Path, Path | None]:
    cfg = load_config(config_path)
    ex = cfg.get("excel", {})
    mirror = ex.get("mirror")
    return Path(ex.get("path", "output/jobs.xlsx")), Path(mirror) if mirror else None


# ------------------------------------------------------------------ create
def add_status_validation(ws, last_row: int) -> None:
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    dv.add(f"U{FIRST_ROW}:U{max(214, last_row)}")
    ws.add_data_validation(dv)


def band_formula(r: int, rubric: dict) -> str:
    hi, med = rubric["bands"]["high"], rubric["bands"]["medium"]
    return f'=IF(B{r}>={hi},"High",IF(B{r}>={med},"Medium","Low"))'


def build_criteria_sheet(ws, rubric: dict) -> None:
    ws["A1"] = "Scoring criteria (generated from config/rubric.yaml: edit the YAML, not this sheet)"
    ws["A1"].font = Font(name="Arial", size=12, bold=True)
    row = 3
    ws.cell(row, 1, "Criterion").font = BOLD_FONT
    ws.cell(row, 2, "Max points").font = BOLD_FONT
    for key, mx in rubric["criteria"].items():
        row += 1
        ws.cell(row, 1, key).font = BASE_FONT
        ws.cell(row, 2, mx).font = BASE_FONT
    row += 2
    ws.cell(row, 1, "Bands").font = BOLD_FONT
    ws.cell(row + 1, 1, f"High: final score >= {rubric['bands']['high']}")
    ws.cell(row + 2, 1, f"Medium: {rubric['bands']['medium']}-{rubric['bands']['high'] - 1}")
    ws.cell(row + 3, 1, f"Low: below {rubric['bands']['medium']}")
    row += 5
    ws.cell(row, 1, "Caps (final = min(raw, cap))").font = BOLD_FONT
    for cap, reasons in sorted(rubric["caps"].items()):
        for text in reasons:
            row += 1
            ws.cell(row, 1, f"Cap {cap}").font = BASE_FONT
            c = ws.cell(row, 2, text)
            c.font, c.alignment = BASE_FONT, LEFT
    row += 2
    ws.cell(row, 1, "Other rules").font = BOLD_FONT
    for text in rubric.get("soft_rules", []):
        row += 1
        c = ws.cell(row, 2, text)
        c.font, c.alignment = BASE_FONT, LEFT
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 110


def create(path: Path, mirror: Path | None = None, force: bool = False) -> None:
    if path.exists() and not force:
        print(f"Already exists: {path} (use --force to recreate)")
        return
    rubric = load_rubric()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws["A1"] = "Job Radar: scored job ads"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["A2"] = ("Partial scores (blue) are judgments of the LLM and can be corrected by hand: the final score recalculates. "
                "Criteria, weights and caps are in the Criteria sheet.")
    ws["A3"] = "Last automatic update: never"
    for i, (header, width) in enumerate(COLUMNS, start=1):
        c = ws.cell(4, i, header)
        c.font, c.fill, c.alignment, c.border = HEADER_FONT, HEADER_FILL, HEADER_ALIGN, BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[4].height = 42
    apply_sheet_format(ws, 4, rubric)

    build_criteria_sheet(wb.create_sheet(SHEET_CRITERIA), rubric)
    sc = wb.create_sheet(SHEET_DISCARDED)
    for i, h in enumerate(DISCARDED_HEADER, start=1):
        c = sc.cell(1, i, h)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, HEADER_ALIGN
    for k, v in {"A": 12, "B": 22, "C": 24, "D": 40, "E": 50, "F": 60, "G": 16}.items():
        sc.column_dimensions[k].width = v

    wb.calculation.fullCalcOnLoad = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if mirror:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, mirror)
    print(f"Created: {path}")


# ------------------------------------------------------------------ rows
def read_rows(ws) -> list[dict]:
    rows = []
    for r in range(FIRST_ROW, ws.max_row + 1):
        if not ws.cell(r, 3).value and not ws.cell(r, 4).value:
            continue
        row = {key: ws[f"{col}{r}"].value for col, key in TEXT_COLS.items()}
        row.update({key: ws[f"{col}{r}"].value for key, col in PARTIAL_COLS.items()})
        row["cap"] = ws[f"P{r}"].value
        link = ws[f"T{r}"]
        row["link"] = (link.hyperlink.target if link.hyperlink else None) or link.value
        row["status"] = row.get("status") or STATUSES[0]
        rows.append(row)
    return rows


def write_row(ws, r: int, row: dict, rubric: dict) -> None:
    values = {"A": band_formula(r, rubric), "B": f'=IF(P{r}="",O{r},MIN(O{r},P{r}))', "O": f"=SUM(I{r}:N{r})",
              "P": row.get("cap") if row.get("cap") not in ("",) else None, "T": row.get("link")}
    values.update({col: row.get(key) for col, key in TEXT_COLS.items()})
    values.update({col: row.get(key) for key, col in PARTIAL_COLS.items()})
    values["U"] = row.get("status") or STATUSES[0]
    for col, val in values.items():
        c = ws[f"{col}{r}"]
        c.value = val
        c.border = BORDER
        if col in "AB":
            c.font, c.alignment = BOLD_FONT, CENTER
        elif col in "IJKLMN":
            c.font, c.alignment = BLUE_FONT, CENTER
        elif col in "OP":
            c.font, c.alignment = BASE_FONT, CENTER
        elif col == "T":
            c.font, c.alignment = LINK_FONT, LEFT
            c.hyperlink = val if val else None
        else:
            c.font, c.alignment = BASE_FONT, LEFT
    ws.row_dimensions[r].height = 135


def apply_sheet_format(ws, last: int, rubric: dict) -> None:
    ws.conditional_formatting = type(ws.conditional_formatting)()
    hi, med = rubric["bands"]["high"], rubric["bands"]["medium"]
    if last >= FIRST_ROW:
        rng = f"A{FIRST_ROW}:{LAST_COL}{last}"
        for formula, color in ((f"$B{FIRST_ROW}>={hi}", "FFC6EFCE"),
                               (f"AND($B{FIRST_ROW}>={med},$B{FIRST_ROW}<{hi})", "FFFFEB9C"),
                               (f"$B{FIRST_ROW}<{med}", "FFFFC7CE")):
            ws.conditional_formatting.add(rng, FormulaRule(formula=[formula], fill=PatternFill("solid", fgColor=color, bgColor=color)))
    for dv in list(ws.data_validations.dataValidation):
        ws.data_validations.dataValidation.remove(dv)
    add_status_validation(ws, last)
    ws.auto_filter.ref = f"A4:{LAST_COL}{max(last, 4)}"
    ws.freeze_panes = f"E{FIRST_ROW}"


# ------------------------------------------------------------------ save and verify
def save_with_fallback(wb, main: Path, mirror: Path | None, new_rows: list[dict], retries: int = 3, wait: int = 60) -> dict:
    out = {"main": None, "mirror": None, "csv": None, "identical_copies": mirror is None, "warnings": []}
    for attempt in range(retries):
        try:
            wb.save(main)
            out["main"] = str(main)
            break
        except PermissionError:
            out["warnings"].append(f"main file locked (attempt {attempt + 1} of {retries})")
            if attempt < retries - 1:
                time.sleep(wait)
    if out["main"]:
        if mirror:
            shutil.copy2(main, mirror)
            out["mirror"] = str(mirror)
            out["identical_copies"] = sha(main) == sha(mirror)
        return out
    if mirror:
        try:
            wb.save(mirror)
            out["mirror"] = str(mirror)
            out["warnings"].append("SAVED ONLY IN THE MIRROR: the main file is stale, the two copies differ")
            return out
        except Exception as e:
            out["warnings"].append(f"mirror save failed too ({e})")
    csv_path = main.parent / f"new-ads-{datetime.now():%Y-%m-%d}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(new_rows[0].keys()) if new_rows else ["empty"])
        w.writeheader()
        w.writerows(new_rows)
    out["csv"] = str(csv_path)
    out["warnings"].append("Excel save failed: new rows written to CSV")
    return out


def verify(path: Path) -> list[str]:
    """Formulas intact, points inside maxima, descending order, valid links."""
    rubric = load_rubric()
    errs: list[str] = []
    ws = openpyxl.load_workbook(path)[SHEET]
    prev = None
    mx = maxima(rubric)
    for r in range(FIRST_ROW, ws.max_row + 1):
        if not ws[f"C{r}"].value:
            continue
        if ws[f"A{r}"].value != band_formula(r, rubric):
            errs.append(f"band formula row {r}")
        if ws[f"B{r}"].value != f'=IF(P{r}="",O{r},MIN(O{r},P{r}))' or ws[f"O{r}"].value != f"=SUM(I{r}:N{r})":
            errs.append(f"score formulas row {r}")
        ev = {k: ws[f"{c}{r}"].value for k, c in PARTIAL_COLS.items()}
        ev["cap"] = ws[f"P{r}"].value
        for k, m in mx.items():
            if not 0 <= float(ev[k] or 0) <= m:
                errs.append(f"{k} row {r} over the maximum")
        s = final_score(ev)
        if prev is not None and s > prev + 1e-9:
            errs.append(f"order not descending at row {r}")
        prev = s
        if not str(ws[f"T{r}"].value or "").startswith("https://"):
            errs.append(f"link row {r}")
    return errs


def write(path: Path, mirror: Path | None = None, evaluations: Path | None = None, discards: Path | None = None,
          override: Path | None = None, retries: int = 3, wait: int = 60) -> dict:
    rubric = load_rubric()
    src = path if path.exists() else (mirror if mirror and mirror.exists() else None)
    if src is None:
        raise FileNotFoundError(f"{path} does not exist: run `python -m jobradar.excel create` first")
    warnings = [] if src == path else ["main file missing: used the mirror"]
    if mirror and path.exists() and mirror.exists():
        a = openpyxl.load_workbook(path, read_only=True)[SHEET]["A3"].value
        b = openpyxl.load_workbook(mirror, read_only=True)[SHEET]["A3"].value
        if a != b:
            warnings.append(f"the two copies differed at the start (main: {a}; mirror: {b})")
    wb = openpyxl.load_workbook(src)
    ws = wb[SHEET]

    new = json.loads(evaluations.read_text(encoding="utf-8")) if evaluations else []
    for ev in new:
        validate(ev, rubric)
        ev.setdefault("status", STATUSES[0])
        ev.setdefault("notes", None)
    existing = read_rows(ws)

    n_override = 0
    if override:
        ov = {str(o["link"]): o for o in json.loads(override.read_text(encoding="utf-8"))}
        for row in existing:
            o = ov.get(str(row.get("link")))
            if not o:
                continue
            row.update(o.get("set") or {})
            for key in ("gaps", "rationale"):
                if o.get("append_" + key):
                    row[key] = (str(row.get(key) or "") + " " + o["append_" + key]).strip()
            validate(row, rubric)
            n_override += 1

    known = {str(r.get("link")) for r in existing}
    added = [ev for ev in new if str(ev.get("link")) not in known]
    rows = sorted(existing + added, key=final_score, reverse=True)

    if ws.max_row >= FIRST_ROW:
        ws.delete_rows(FIRST_ROW, ws.max_row - FIRST_ROW + 1)
    for i, row in enumerate(rows):
        write_row(ws, FIRST_ROW + i, row, rubric)
    apply_sheet_format(ws, FIRST_ROW - 1 + len(rows), rubric)
    ws["A3"] = f"Last automatic update: {datetime.now():%Y-%m-%d %H:%M}"

    sc = wb[SHEET_DISCARDED]
    evaluated_links = {str(v.get("link")) for v in added}
    for r in range(sc.max_row, 1, -1):  # retried ads that are now scored leave the Discarded sheet
        if str(sc.cell(r, 6).value) in evaluated_links:
            sc.delete_rows(r)
    n_discards = 0
    if discards:
        seen = {str(sc.cell(r, 6).value) for r in range(2, sc.max_row + 1)}
        for d in json.loads(discards.read_text(encoding="utf-8")):
            if str(d.get("link")) in seen:
                continue
            sc.append([datetime.now().strftime("%Y-%m-%d"), d.get("id") or "", d.get("company"), d.get("role"),
                       d.get("reason"), d.get("link"), d.get("source")])
            n_discards += 1

    wb.calculation.fullCalcOnLoad = True
    saved = save_with_fallback(wb, path, mirror, added, retries, wait)
    target = Path(saved["main"] or saved["mirror"]) if (saved["main"] or saved["mirror"]) else None
    errors = verify(target) if target else ["no spreadsheet saved"]
    return {
        "added": len(added), "already_present": len(new) - len(added), "overrides_applied": n_override,
        "total_rows": len(rows), "discards_added": n_discards, "verification": errors or "ok",
        "warnings": warnings + saved["warnings"], **{k: saved[k] for k in ("main", "mirror", "csv", "identical_copies")},
        "best_new": [{"score": final_score(v), "company": v["company"], "role": v["role"], "band": band(final_score(v), rubric)}
                     for v in sorted(added, key=final_score, reverse=True)[:5]],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Job Radar spreadsheet")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--force", action="store_true")
    w = sub.add_parser("write")
    w.add_argument("--evaluations", type=Path)
    w.add_argument("--discards", type=Path)
    w.add_argument("--override", type=Path)
    args = ap.parse_args(argv)
    path, mirror = resolve_paths(args.config)
    if args.cmd == "create":
        create(path, mirror, args.force)
    else:
        print(json.dumps(write(path, mirror, args.evaluations, args.discards, args.override), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
