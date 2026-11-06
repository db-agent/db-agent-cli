from datetime import datetime
from pathlib import Path

from dbagent_sqlserver import classify, ssrs, usage

CFG = classify.load_config()


def run(examples, csv=None, cfg=CFG):
    u = usage.load_csv(examples / csv) if csv else None
    res = ssrs.from_folder(examples / "rdl", u, cfg)
    return {r["name"]: r for r in res["reports"]}, res


def test_full_year_log_gives_all_three_classes(examples):
    reps, res = run(examples, "usage.csv")
    assert res["classification_counts"] == {"move": 5, "review": 5, "retire": 2}
    assert reps["LegacyInventory"]["classification"] == "retire"   # never ran
    assert reps["OldPriceList"]["classification"] == "retire"      # last ran 470 days ago
    assert reps["SalesByTerritory"]["classification"] == "move"


def test_review_reasons_are_stated(examples):
    reps, _ = run(examples)
    reasons = lambda n: "; ".join(reps[n]["reasons"])  # noqa: E731
    assert "Subreports" in reasons("CustomerOrders")
    assert "Custom code" in reasons("EmployeeDirectory")
    assert "data-driven" in reasons("MonthlyCommissions")
    assert "could not be fully parsed" in reasons("VendorSpend")


def test_short_log_never_produces_retire(examples):
    reps, res = run(examples, "usage_60days.csv")
    assert res["classification_counts"].get("retire", 0) == 0
    assert "covers only" in "; ".join(reps["LegacyInventory"]["reasons"])
    assert reps["LegacyInventory"]["classification"] == "review"


def test_no_usage_never_produces_retire_and_says_so(examples):
    reps, res = run(examples)
    assert res["classification_counts"].get("retire", 0) == 0
    assert res["usage"]["note"] and reps["SalesByTerritory"]["usage_known"] is False


def test_thresholds_and_switches_come_from_config(examples, tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("low_usage_runs_365: 0\nreview_if:\n  subreports: false\n  custom_code: false\n")
    cfg = classify.load_config(cfg_file)
    reps, _ = run(examples, "usage.csv", cfg)
    assert reps["CustomerOrders"]["classification"] == "move"
    assert reps["EmployeeDirectory"]["classification"] == "move"
    assert reps["QuarterlyTaxSummary"]["classification"] == "move"


def test_usage_counts(examples):
    u = usage.load_csv(examples / "usage.csv")
    s = u.get("/Sales/SalesByTerritory")
    assert s.runs_365 > s.runs_90 > 0 and s.distinct_users > 1
    assert u.get("/Does/Not/Exist").runs_365 == 0


def test_usage_csv_needs_required_columns(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("Path,When\n/a,2026-01-01\n")
    try:
        usage.load_csv(p)
    except ValueError as e:
        assert "ItemPath" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_as_of_changes_the_windows(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("ItemPath,UserName,TimeStart,TimeEnd,Status\n/a,u1,2026-01-10 10:00:00,2026-01-10 10:00:05,rsSuccess\n/a,u2,2025-03-01 10:00:00,2025-03-01 10:00:05,rsSuccess\n")
    u = usage.load_csv(p, as_of=datetime(2026, 2, 1))
    assert (u.get("/a").runs_90, u.get("/a").runs_365, u.get("/a").distinct_users) == (1, 2, 2)
    assert u.window_days == (datetime(2026, 2, 1) - datetime(2025, 3, 1, 10)).days
