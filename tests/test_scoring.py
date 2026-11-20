"""Email alerts, rubric validation, spreadsheet, optional API path."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import openpyxl
import pytest

from jobradar import excel
from jobradar.email_alerts import load_alerts, parse_alert_html
from jobradar.rubric import band, final_score, load_rubric, validate
from jobradar.scoring_api import build_prompt, extract_json_array, score_ads

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EVALS = json.loads((EXAMPLES / "evaluations_example.json").read_text(encoding="utf-8"))


# ---------------- email alerts ----------------
def test_alert_parser_extracts_ads():
    jobs = parse_alert_html((EXAMPLES / "alert_sample.html").read_text(encoding="utf-8"))
    assert [j.external_id for j in jobs] == ["3900000001", "3900000002", "3900000003"]
    first = jobs[0]
    assert first.job_title == "Revenue Operations Analyst" and first.company_name == "Northwind Analytics"
    assert first.location_raw.startswith("Milan") and first.url_origin == "https://www.linkedin.com/jobs/view/3900000001/"
    assert jobs[1].company_name == "Contoso Cloud" and jobs[1].location_raw == "Remote, Europe"
    assert jobs[2].location_raw.startswith("Amsterdam")


def test_alert_parser_ignores_labels_and_buttons():
    jobs = parse_alert_html((EXAMPLES / "alert_sample.html").read_text(encoding="utf-8"))
    for j in jobs:
        assert j.company_name.lower() not in {"easy apply", "actively recruiting", "view job"}


def test_load_alerts_dedups_by_id(tmp_path):
    html = (EXAMPLES / "alert_sample.html").read_text(encoding="utf-8")
    (tmp_path / "a.html").write_text(html, encoding="utf-8")
    (tmp_path / "b.html").write_text(html, encoding="utf-8")
    assert len(load_alerts(tmp_path)) == 3
    assert load_alerts(tmp_path / "missing") == []


# ---------------- rubric ----------------
def test_examples_are_valid_and_banded():
    rubric = load_rubric()
    scores = {e["company"]: final_score(e) for e in EVALS}
    for e in EVALS:
        validate(e, rubric)
    assert scores["Northwind Analytics"] == 77 and band(77) == "High"
    assert scores["Globex Gmbh"] == 40 and band(40) == "Low"          # language cap: raw 62 -> 40
    assert scores["Initech Ltd"] == 40                                 # UK-only cap
    assert scores["Umbrella Software"] == 58 and band(58) == "Medium"  # raw below the 60 cap


@pytest.mark.parametrize("change", [
    {"skills": 31}, {"role_pts": -1}, {"location_pts": 16}, {"cap": 50}, {"link": "http://insecure.example"}, {"rationale": ""},
])
def test_validate_rejects_bad_records(change):
    ev = {**copy.deepcopy(EVALS[0]), **change}
    with pytest.raises(ValueError):
        validate(ev)


# ---------------- spreadsheet ----------------
@pytest.fixture()
def sheet(tmp_path):
    main, mirror = tmp_path / "jobs.xlsx", tmp_path / "mirror" / "jobs.xlsx"
    excel.create(main, mirror)
    return main, mirror


def write_eval_file(tmp_path, evals, name="ev.json"):
    p = tmp_path / name
    p.write_text(json.dumps(evals), encoding="utf-8")
    return p


def test_write_sorts_descending_and_mirrors(tmp_path, sheet):
    main, mirror = sheet
    res = excel.write(main, mirror, write_eval_file(tmp_path, EVALS), EXAMPLES / "discards_example.json", wait=0)
    assert res["added"] == 5 and res["total_rows"] == 5 and res["verification"] == "ok" and res["identical_copies"]
    ws = openpyxl.load_workbook(main)[excel.SHEET]
    companies = [ws[f"C{r}"].value for r in range(5, 10)]
    assert companies[0] == "Northwind Analytics" and companies[1] == "Contoso Cloud"
    assert ws["A5"].value.startswith('=IF(B5>=70,"High"')
    assert ws["P7"].value in (None, 60, 40)
    assert openpyxl.load_workbook(main)[excel.SHEET_DISCARDED].max_row == 3


def test_write_is_idempotent_and_keeps_user_columns(tmp_path, sheet):
    main, mirror = sheet
    f = write_eval_file(tmp_path, EVALS)
    excel.write(main, mirror, f, wait=0)
    wb = openpyxl.load_workbook(main)
    wb[excel.SHEET]["U6"].value = "Applied"       # the user marks a row
    wb[excel.SHEET]["V6"].value = "sent CV on Monday"
    wb.save(main)
    company = openpyxl.load_workbook(main)[excel.SHEET]["C6"].value
    res = excel.write(main, mirror, f, wait=0)   # same evaluations again
    assert res["added"] == 0 and res["already_present"] == 5 and res["total_rows"] == 5
    ws = openpyxl.load_workbook(main)[excel.SHEET]
    row = next(r for r in range(5, 10) if ws[f"C{r}"].value == company)
    assert ws[f"U{row}"].value == "Applied" and ws[f"V{row}"].value == "sent CV on Monday"


def test_override_applies_cap_and_resorts(tmp_path, sheet):
    main, mirror = sheet
    excel.write(main, mirror, write_eval_file(tmp_path, EVALS), wait=0)
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps([{"link": "https://example.com/jobs/contoso-sales-ops", "set": {"cap": 40},
                               "append_gaps": "Rule applied later.", "append_rationale": "Cap 40 added."}]), encoding="utf-8")
    res = excel.write(main, mirror, override=ov, wait=0)
    assert res["overrides_applied"] == 1 and res["verification"] == "ok"
    ws = openpyxl.load_workbook(main)[excel.SHEET]
    contoso = next(r for r in range(5, 10) if ws[f"C{r}"].value == "Contoso Cloud")
    assert ws[f"P{contoso}"].value == 40 and "Rule applied later." in ws[f"R{contoso}"].value
    assert contoso > 5  # no longer second: it dropped below the uncapped rows


def test_write_rejects_invalid_evaluation(tmp_path, sheet):
    main, mirror = sheet
    bad = copy.deepcopy(EVALS)
    bad[0]["skills"] = 99
    with pytest.raises(ValueError):
        excel.write(main, mirror, write_eval_file(tmp_path, bad), wait=0)


def test_locked_main_falls_back_to_mirror(tmp_path, sheet, monkeypatch):
    main, mirror = sheet
    real_save = openpyxl.Workbook.save

    def locked(self, filename):
        if Path(filename) == main:
            raise PermissionError("file open in Excel")
        return real_save(self, filename)

    monkeypatch.setattr(openpyxl.Workbook, "save", locked)
    res = excel.write(main, mirror, write_eval_file(tmp_path, EVALS), retries=2, wait=0)
    assert res["main"] is None and res["mirror"] == str(mirror) and not res["identical_copies"]
    assert any("SAVED ONLY IN THE MIRROR" in w for w in res["warnings"])


# ---------------- optional API path ----------------
class FakeClient:
    class _Block:
        def __init__(self, text):
            self.text = text

    class _Reply:
        def __init__(self, text):
            self.content = [FakeClient._Block(text)]

    def __init__(self, payload):
        self.payload, self.prompts = payload, []
        self.messages = self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return FakeClient._Reply("Here you go:\n```json\n" + json.dumps(self.payload) + "\n```")


def test_api_path_validates_model_output():
    bad = {**copy.deepcopy(EVALS[0]), "skills": 99}
    client = FakeClient([EVALS[0], bad])
    valid, problems = score_ads([{"url": "https://example.com/x", "source": "S", "title": "T"}], {"name": "x"}, client=client)
    assert len(valid) == 1 and len(problems) == 1 and "skills" in problems[0]
    assert "cap 40" in client.prompts[0] and "United Kingdom" in client.prompts[0]


def test_extract_json_array_tolerates_chatter():
    assert extract_json_array('noise [ {"a": 1} ] more') == [{"a": 1}]
    with pytest.raises(ValueError):
        extract_json_array("no array here")


def test_prompt_contains_rubric_caps_and_ads():
    rb = load_rubric()
    p = build_prompt({"name": "Alex"}, rb, [{"url": "https://example.com/x"}])
    assert "cap 40" in p and "cap 45" in p and "cap 60" in p and "https://example.com/x" in p
