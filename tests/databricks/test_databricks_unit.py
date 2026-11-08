from datetime import date

import pytest

from dbagent.engines.databricks import checks, cost, queries, runtimes, sqlguard

TODAY = date(2026, 11, 7)


# ---- runtimes ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("sv,state", [
    ("13.3.x-scala2.12", "ended"), ("13.3.x-cpu-ml-scala2.12", "ended"), ("14.3.x-photon-scala2.12", "ending_soon"),
    ("15.4.x-scala2.12", "supported"), ("12.2.x-scala2.12", "ended"), ("10.4.x-scala2.12", "ended"),
    ("apache-spark-2.4.x-scala2.11", "ended"), ("custom:foo", "unknown"), ("", "unknown"), (None, "unknown"),
    ("99.0.x-scala2.12", "unknown"),
])
def test_runtime_states(sv, state):
    assert runtimes.classify(sv, TODAY).state == state


def test_runtime_dates_from_the_docs():
    r = runtimes.classify("14.3.x-scala2.12", TODAY)
    assert r.eos == date(2027, 2, 1) and r.label == "14.3 LTS" and r.days_left == 86
    assert runtimes.classify("13.3.x-scala2.12", TODAY).eos == date(2026, 8, 22)


def test_warning_window_is_configurable():
    assert runtimes.classify("14.3.x-scala2.12", TODAY, warn_days=30).state == "supported"


# ---- guard ------------------------------------------------------------------------------------------
def test_every_query_the_tool_sends_passes_the_guard():
    ws = "1234567890"
    for sql in [queries.cost_by_month(date(2026, 9, 1), date(2026, 12, 1), ws), queries.cost_by_month(date(2026, 9, 1), date(2026, 12, 1), None),
                queries.spend_by_cluster(30, ws), queries.spend_by_warehouse(30, None), queries.queries_by_warehouse(30, ws),
                queries.untagged_spend(30, ws), queries.dbfs_mount_events(365, ws), queries.SHOW_SCHEMAS, queries.show_tables("sales"),
                queries.show_tables("my-schema")]:
        sqlguard.check(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE x", "INSERT INTO t VALUES (1)", "SELECT 1; DROP TABLE x", "SELECT * FROM t; SELECT 2", "/* ok */ DELETE FROM t",
    "WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a", "SHOW TABLES IN main.default", "SHOW GRANTS ON t", "SET x = 1",
    "OPTIMIZE t", "VACUUM t", "USE CATALOG main", "SELECT 1 -- x\n; DROP TABLE t", "CREATE TABLE t (a int)", "COPY INTO t FROM 's3://x'",
    "SHOW TABLES IN hive_metastore.a; DROP TABLE b", "", "  ;  ", "SELECT 'unterminated",
])
def test_guard_refuses(sql):
    with pytest.raises(sqlguard.GuardError):
        sqlguard.check(sql)


def test_guard_ignores_keywords_inside_literals_and_comments():
    sqlguard.check("SELECT 'drop table; insert' AS x /* delete */ -- update\n FROM system.billing.usage")


def test_workspace_id_must_be_numeric():
    with pytest.raises(ValueError):
        queries.spend_by_cluster(30, "1' OR '1'='1")
    with pytest.raises(ValueError):
        queries.show_tables("a`; DROP TABLE x; --")


# ---- cost -------------------------------------------------------------------------------------------
def test_month_helpers():
    assert cost.default_range(TODAY) == ("2026-09", "2026-11")
    assert cost.default_range(date(2026, 1, 15)) == ("2025-11", "2026-01")
    assert [d.isoformat() for d in cost.month_list("2026-11", "2027-01")] == ["2026-11-01", "2026-12-01", "2027-01-01"]
    with pytest.raises(ValueError):
        cost.month_list("2026-11", "2026-09")



def test_cost_split():
    import dbx_fakes
    res = cost.analyze(dbx_fakes.SQL["cost_by_month"], TODAY)
    sep, octo, nov = res["months"]
    assert res["tier_changed"] and sep["dominant_tier"] == "STANDARD" and octo["dominant_tier"] == "PREMIUM"
    assert nov["partial"] and not octo["partial"]
    v = octo["vs_first"]
    assert v["cost_per_day_change"] == pytest.approx(65 / 31 - 55 / 30)
    assert v["volume_effect_per_day"] + v["price_effect_per_day"] == pytest.approx(v["cost_per_day_change"])
    assert v["volume_effect_per_day"] == pytest.approx(100 / 31 * 0.55 - 55 / 30)
    assert v["price_effect_per_day"] == pytest.approx(65 / 31 - 100 / 31 * 0.55)


def test_cost_handles_missing_prices_and_no_rows():
    res = cost.analyze([{"month": "2026-09-01", "sku_name": "X", "dbus": "10", "cost": None, "days": "5"}], TODAY)
    assert res["months"][0]["unpriced_dbus"] == 10 and res["months"][0]["rate"] is None
    assert cost.analyze([], TODAY)["months"] == []


# ---- checks -----------------------------------------------------------------------------------------
def test_check_functions_on_edge_inputs():
    assert checks.runtime_findings([], [], TODAY, 180)[0].id == "runtime.summary"
    assert checks.hms_findings({}) == [] and checks.hms_findings({"default": 0}) == []
    assert checks.mount_findings(None, 365) == [] and checks.untagged_findings([], 30) == []
    assert checks.untagged_findings([{"product": "X", "cost": "0", "untagged_cost": "0"}], 30) == []
    assert checks.no_autotermination([{"cluster_id": "a", "cluster_source": "JOB", "autotermination_minutes": 0}], None, 30) == []
    assert checks.no_autotermination([{"cluster_id": "a", "cluster_source": "UI"}], None, 30) == []   # key missing: unknown, not flagged
    assert checks.jobs_on_all_purpose([{"job_id": 1, "settings": {}}], [], None, 30) == []


def test_untagged_severity_follows_the_share():
    def sev(untagged):
        return checks.untagged_findings([{"product": "P", "cost": "100", "untagged_cost": str(untagged)}], 30)[0].severity
    assert sev(10) == "low" and sev(25) == "medium" and sev(80) == "high"
