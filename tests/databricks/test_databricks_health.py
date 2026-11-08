import json
import re
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dbx_fakes
from db_agent.cli import app
from db_agent.engines.databricks import health, render

TODAY = date(2026, 11, 7)
SRC = Path(__file__).resolve().parents[2] / "src" / "db_agent" / "engines" / "databricks"
runner = CliRunner()


def run(reader, **kw):
    return health.run(reader, health.Options(TODAY, **kw))


def ids(rep):
    return {f.id: f for f in rep.findings}


def test_full_run_finds_every_requested_category():
    f = ids(run(dbx_fakes.FakeReader()))
    # runtimes
    assert f["runtime.ended"].severity == "high" and [i["where"] for i in f["runtime.ended"].items] == ["cluster legacy-etl (c-old)"]
    assert f["runtime.ending_soon"].items[0]["runtime"] == "14.3 LTS"
    # legacy dependencies
    assert f["legacy.dbfs_init_scripts"].items[0]["script"] == "dbfs:/init/setup.sh"
    assert f["legacy.no_isolation"].items[0]["data_security_mode"] == "NONE"
    assert f["legacy.hive_metastore"].title.startswith("3 tables")          # temporary table not counted
    assert f["legacy.dbfs_mounts"].items[0]["user"] == "a@x.com"
    # cost
    assert "tier changed" in f["cost.mom"].detail and "2026-09=STANDARD then 2026-10=PREMIUM" in f["cost.mom"].detail
    assert "from list price per DBU" in f["cost.mom"].detail
    # waste
    assert [r["job"] for r in f["waste.jobs_on_all_purpose"].items] == ["nightly"]               # the paused job is not scheduled
    assert f["waste.jobs_on_all_purpose"].items[0]["cluster_spend_30d"] == 630.5
    assert f["waste.no_autotermination"].severity == "high"                                       # c-old is RUNNING
    assert f["waste.idle_warehouses"].items[0]["id"] == "w-idle"
    assert f["waste.warehouse_no_autostop"].items[0]["id"] == "w-idle"
    assert f["waste.untagged_spend"].title.startswith("50% of spend")


def test_the_job_cluster_on_14_3_is_the_only_one_ending_soon():
    f = ids(run(dbx_fakes.FakeReader()))
    assert len(f["runtime.ending_soon"].items) == 1 and "job modern" in f["runtime.ending_soon"].items[0]["where"]


def test_disabled_hive_metastore_is_a_finding_not_a_failure():
    rep = run(dbx_fakes.FakeReader(hms_disabled=True))
    f = ids(rep)
    assert f["legacy.hive_metastore_disabled"].severity == "info" and "legacy.hive_metastore" not in f
    assert not any("Hive" in n["check"] for n in rep.not_collected)


def test_cost_text_is_readable_and_flags_a_thin_baseline():
    rows = [{"month": "2026-09-01", "sku_name": "PREMIUM_SQL", "dbus": "23", "cost": "7.4", "days": "5"},
            {"month": "2026-10-01", "sku_name": "PREMIUM_SQL", "dbus": "175", "cost": "73", "days": "27"}]
    from db_agent.engines.databricks import cost as cost_mod
    d = health._cost_finding(cost_mod.analyze(rows, TODAY), "2026-09", "2026-10").detail
    assert "Weak baseline" in d and "$-" not in d and "$+" not in d and "(volume and SKU mix)" in d and "+$" in d


def test_one_denied_system_table_does_not_hide_the_rest():
    rep = run(dbx_fakes.FakeReader(fail={"untagged_spend", "queries_by_warehouse"}))
    f = ids(rep)
    assert {n["check"] for n in rep.not_collected} == {"untagged spend", "queries by warehouse (system.query.history)"}
    assert "waste.untagged_spend" not in f and "cost.mom" in f and "waste.no_autotermination" in f
    # without query history the idle-warehouse claim must not be made
    assert "waste.idle_warehouses" not in f and "waste.warehouse_no_autostop" in f


def test_without_a_warehouse_only_rest_checks_run_and_it_says_so():
    rep = run(dbx_fakes.FakeReader(sql=False))
    f = ids(rep)
    assert any("no SQL warehouse" in n["reason"] for n in rep.not_collected)
    assert "runtime.ended" in f and "legacy.dbfs_init_scripts" in f and "waste.no_autotermination" in f
    assert "cost.mom" not in f and "legacy.hive_metastore" not in f


def test_every_statement_sent_is_filtered_to_this_workspace_by_default():
    r = dbx_fakes.FakeReader()
    run(r)
    billing = [s for s in r.statements if "system.billing" in s or "system.query" in s or "system.access" in s]
    assert billing and all("'1234567890'" in s for s in billing)
    r2 = dbx_fakes.FakeReader()
    run(r2, all_workspaces=True)
    assert not any("workspace_id =" in s for s in r2.statements)


def test_empty_workspace_gives_no_findings_but_does_not_crash():
    rep = run(dbx_fakes.FakeReader(clusters=[], jobs=[], warehouses=[]))
    assert all(f.severity == "info" or f.id.startswith(("cost", "legacy", "waste")) for f in rep.findings)


def test_json_and_markdown_render(tmp_path):
    rep = run(dbx_fakes.FakeReader(fail={"dbfs_mount_events"}))
    paths = render.write(rep, tmp_path, {"md", "json"})
    data = json.loads(paths[0].read_text())
    assert data["counts"]["high"] >= 3 and data["not_collected"][0]["check"].startswith("DBFS mount")
    md = paths[1].read_text()
    assert "## Spend waste" in md and "## Not collected" in md and "Monthly figures" in md and "partial" in md


# ---- architecture ------------------------------------------------------------------------------------
def test_only_client_imports_the_sdk():
    for p in SRC.glob("*.py"):
        has = re.search(r"^\s*(import|from)\s+databricks\b", p.read_text(), re.M)
        assert bool(has) == (p.name == "client.py"), p.name


def test_client_only_makes_read_calls():
    text = (SRC / "client.py").read_text()
    calls = set(re.findall(r"self\._ws\.((?:\w+\.)?\w+)\(", text))
    allowed = {"get_workspace_id", "clusters.list", "jobs.list", "warehouses.list", "statement_execution.execute_statement",
               "statement_execution.get_statement", "statement_execution.cancel_execution"}
    assert calls <= allowed, calls - allowed
    assert "sqlguard.check(statement)" in text and text.index("sqlguard.check(statement)") < text.index("execute_statement(")


def test_no_http_libraries_and_no_token_option():
    for p in SRC.glob("*.py"):
        t = p.read_text()
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp)\b", t, re.M), p.name
    assert "--token" not in (SRC / "cli.py").read_text().replace("Tokens are never", "")


# ---- CLI ---------------------------------------------------------------------------------------------
def test_cli_end_to_end_with_a_fake_workspace(tmp_path, monkeypatch):
    import db_agent.engines.databricks.client as client
    monkeypatch.setattr(client, "connect", lambda *a, **k: dbx_fakes.FakeReader())
    res = runner.invoke(app, ["databricks", "health", "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "high" in res.output and (tmp_path / "databricks-health.json").exists() and (tmp_path / "databricks-health.md").exists()


def test_cli_reports_a_connection_failure(monkeypatch):
    import db_agent.engines.databricks.client as client
    def boom(*a, **k): raise RuntimeError("token not found")
    monkeypatch.setattr(client, "connect", boom)
    res = runner.invoke(app, ["databricks", "health"])
    assert res.exit_code == 2 and "Could not connect" in res.output


@pytest.mark.parametrize("args", [["--format", "xlsx"], ["--cost-from", "2026-09"], ["--cost-from", "2026-11", "--cost-to", "2026-09"]])
def test_cli_rejects_bad_options(args):
    assert runner.invoke(app, ["databricks", "health", *args]).exit_code == 2
