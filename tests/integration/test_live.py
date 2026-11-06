import csv
import json
import re
import uuid
from pathlib import Path

import pymssql
import pytest

from dbagent_sqlserver import classify, inventory, outputs, risks as R, ssrs as ssrs_mod, usage as usage_mod, validate as V
from dbagent_sqlserver.db import DatabaseError, SafeConnection
from dbagent_sqlserver.guard import GuardError, QueryGuard

from .conftest import HOST, PASSWORD, SRC_PORT, TGT_PORT, cfg

pytestmark = pytest.mark.integration
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class Recording(QueryGuard):
    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    def check(self, sql):
        self.seen.append(sql)
        super().check(sql)


def test_inventory_on_sql_server_2017(src):
    guard = Recording()
    inv = inventory.collect(src, databases=["AdventureWorks2017"], guard=guard)
    assert inv["server"]["product_version"].split(".")[0] in ("14", "15")  # 15 = Azure SQL Edge (Apple Silicon)
    db = inv["objects"]["AdventureWorks2017"]
    assert len(db["tables"]) >= 70 and len(db["views"]) >= 15 and len(db["modules"]) >= 20
    soh = next(t for t in db["tables"] if (t["schema"], t["name"]) == ("Sales", "SalesOrderHeader"))
    assert soh["rows"] == 31465 and any(c["column_name"] == "TotalDue" for c in soh["columns"]) and soh["indexes"]
    # No statement ever read rows from a business table
    business = {"SalesOrderHeader", "Person", "Employee", "Product", "SalesOrderDetail", "Customer"}
    for sql in guard.seen:
        assert not (business & set(re.findall(r"\b\w+\b", sql))) or "sys." in sql or "sql_modules" in sql, sql[:80]
    assert guard.seen and all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in guard.seen)
    risks = R.assess(inv)
    if inv["server"]["product_version"].split(".")[0] in ("13", "14"):
        assert any(r.id == "end-of-support" for r in risks)
    assert all(r.doc.startswith("https://learn.microsoft.com") for r in risks)
    # AdventureWorks has Flags/Name/etc with the deprecated-type check: report it as data, whatever it finds
    assert isinstance(risks, list)


def test_inventory_also_works_on_sql_server_2022_and_reports_compat_difference(tgt, sa_exec):
    inv = inventory.collect(tgt, databases=["AdventureWorks2017"])
    assert inv["server"]["product_version"].split(".")[0] in ("15", "16")
    d = next(x for x in inv["databases"] if x["name"] == "AdventureWorks2017")
    assert d["compatibility_level"] in (140, 150, 160)


def test_guard_blocks_writes_even_for_sa(src):
    with SafeConnection(src, QueryGuard()) as c:
        for bad in ["SELECT * INTO dbo.leak FROM sys.objects", "DROP TABLE dbo.x", "EXEC sp_who",
                    "SELECT * FROM OPENROWSET('SQLNCLI','Server=x;','SELECT 1')"]:
            with pytest.raises(GuardError):
                c.query(bad)
        assert c.query("SELECT 1 AS one") == [{"one": 1}]


def test_connection_is_rolled_back_and_nothing_is_created(src, sa_exec):
    with SafeConnection(src.with_database("AdventureWorks2017"), QueryGuard()) as c:
        c.query("SELECT COUNT(*) AS n FROM sys.tables")
    assert sa_exec(SRC_PORT, "SELECT COUNT(*) FROM sys.tables WHERE name = 'leak'", "AdventureWorks2017", fetch=True)[0][0] == 0


def test_least_privilege_login_from_permissions_doc(src, sa_exec):
    """The grants in docs/permissions.md are enough for inventory, and not enough to write."""
    login = "dbagent_reader_t"
    pw = "Rd!" + uuid.uuid4().hex[:12] + "Aa1"
    sa_exec(SRC_PORT, f"IF SUSER_ID('{login}') IS NOT NULL DROP LOGIN [{login}]; CREATE LOGIN [{login}] WITH PASSWORD='{pw}', CHECK_POLICY=OFF;"
                      f" GRANT VIEW ANY DEFINITION TO [{login}]; GRANT VIEW SERVER STATE TO [{login}];")
    sa_exec(SRC_PORT, f"CREATE USER [{login}] FOR LOGIN [{login}]; GRANT VIEW DEFINITION TO [{login}]; GRANT VIEW DATABASE STATE TO [{login}];", "AdventureWorks2017")
    try:
        ro = cfg(SRC_PORT, user=login, password=pw)
        inv = inventory.collect(ro, databases=["AdventureWorks2017"])
        db = inv["objects"]["AdventureWorks2017"]
        assert len(db["tables"]) >= 70 and any(t["rows"] for t in db["tables"])
        assert any("Agent" in w or "msdb" in w for w in inv["warnings"])          # msdb not granted: skipped, not fatal
        assert not any("tables" in w for w in inv["warnings"])
        # database level: the login cannot read business data or write, whatever the tool does
        raw = pymssql.connect(server=HOST, port=str(SRC_PORT), user=login, password=pw, database="AdventureWorks2017", login_timeout=60)
        cur = raw.cursor()
        with pytest.raises(pymssql.Error):
            cur.execute("SELECT TOP 1 * FROM Sales.SalesOrderHeader")
        with pytest.raises(pymssql.Error):
            cur.execute("CREATE TABLE dbo.nope (a int)")
        raw.close()
    finally:
        sa_exec(SRC_PORT, f"DROP USER IF EXISTS [{login}]", "AdventureWorks2017")
        sa_exec(SRC_PORT, f"DROP LOGIN [{login}]")


def _folder_reports():
    res = ssrs_mod.from_folder(EXAMPLES / "rdl", usage_mod.load_csv(EXAMPLES / "usage.csv"), classify.load_config())
    return res["reports"]


def test_validate_source_2017_vs_target_2022_passes_then_detects_a_change(src, tgt, sa_exec):
    reports = [r for r in _folder_reports() if r["classification"] == "move"]
    overrides = {"defaults": {}}
    s, t = src.with_database("AdventureWorks2017"), tgt.with_database("AdventureWorks2017")
    rows = V.validate_reports(reports, s, t, overrides, allow_exec=True)
    by = {(r["report"], r["dataset"]): r for r in rows}
    assert by[("/Sales/SalesByTerritory", "Sales")]["status"] == "pass", by[("/Sales/SalesByTerritory", "Sales")]
    assert by[("/Sales/SalesByTerritory", "Sales")]["source_rows"] == by[("/Sales/SalesByTerritory", "Sales")]["target_rows"] > 0
    assert by[("/Sales/TopProducts", "Top")]["status"] == "pass"
    assert by[("/Sales/OrderDetails", "Lines")]["status"] == "pass"
    assert by[("/Inventory/BillOfMaterials", "Bom")]["status"] == "pass"           # stored procedure, --allow-exec
    # a multi-value parameter is skipped with a reason, not guessed
    assert by[("/Inventory/ProductCatalog", "Cat")]["status"] == "skipped" and "multi-value" in by[("/Inventory/ProductCatalog", "Cat")]["reason"]
    # stored procedure without --allow-exec is skipped
    no_exec = V.validate_reports([r for r in reports if r["name"] == "BillOfMaterials"], s, t, overrides, allow_exec=False)
    assert no_exec[0]["status"] == "skipped" and "--allow-exec" in no_exec[0]["reason"]
    # change one value on the target and the validation must notice
    oid = sa_exec(TGT_PORT, "SELECT TOP 1 SalesOrderID FROM Sales.SalesOrderHeader WHERE OrderDate >= '2013-01-01' AND OrderDate < '2014-01-01' ORDER BY SalesOrderID",
                  "AdventureWorks2017", fetch=True)[0][0]          # inside the report's default date range
    sa_exec(TGT_PORT, f"UPDATE Sales.SalesOrderHeader SET SubTotal = SubTotal + 100 WHERE SalesOrderID = {oid}", "AdventureWorks2017")
    try:
        bad = V.validate_reports([r for r in reports if r["name"] == "SalesByTerritory"], s, t, overrides)
        assert bad[0]["status"] == "fail" and "Total total" in bad[0]["differences"]
        assert set(bad[0]) == {"report", "dataset", "status", "reason", "source_rows", "target_rows", "source_ms", "target_ms", "differences"}   # counts and totals only
    finally:
        sa_exec(TGT_PORT, f"UPDATE Sales.SalesOrderHeader SET SubTotal = SubTotal - 100 WHERE SalesOrderID = {oid}", "AdventureWorks2017")


def test_validate_reports_a_missing_object_on_the_target_as_fail(src, tgt, sa_exec):
    rep = {"classification": "move", "path": "/T/Missing", "parameters": [], "datasets": [
        {"name": "d", "shared_reference": None, "command_type": "Text", "command_text": "SELECT COUNT(*) AS n FROM dbo.DoesNotExistOnTarget",
         "query_parameters": [], "confidence": "high", "parse_error": None}]}
    sa_exec(SRC_PORT, "IF OBJECT_ID('dbo.DoesNotExistOnTarget') IS NULL CREATE TABLE dbo.DoesNotExistOnTarget (a int)", "AdventureWorks2017")
    try:
        rows = V.validate_reports([rep], src.with_database("AdventureWorks2017"), tgt.with_database("AdventureWorks2017"))
        assert rows[0]["status"] == "fail" and "target error" in rows[0]["reason"]
    finally:
        sa_exec(SRC_PORT, "DROP TABLE IF EXISTS dbo.DoesNotExistOnTarget", "AdventureWorks2017")


def test_reportserver_mode_against_a_stand_in_reportserver_database(src, sa_exec):
    """The queries and the Catalog/ExecutionLog3 handling run on a real SQL Server.

    The ReportServer database here is a stand-in with the columns this kit reads. It is NOT a real SSRS
    catalog, so this proves the query mechanics, not compatibility with every SSRS version."""
    sa_exec(SRC_PORT, "IF DB_ID('ReportServerStandIn') IS NOT NULL DROP DATABASE ReportServerStandIn; CREATE DATABASE ReportServerStandIn;")
    d = "ReportServerStandIn"
    sa_exec(SRC_PORT, """
        CREATE TABLE dbo.Catalog (ItemID uniqueidentifier PRIMARY KEY, Path nvarchar(425), Name nvarchar(425), Type int, LinkSourceID uniqueidentifier NULL, Content varbinary(max) NULL);
        CREATE TABLE dbo.Subscriptions (Report_OID uniqueidentifier, DeliveryExtension nvarchar(260), DataSettings nvarchar(max) NULL);
        CREATE TABLE dbo.ExecutionLogStore (ItemPath nvarchar(425), UserName nvarchar(260), TimeStart datetime, TimeEnd datetime, Status nvarchar(32), RequestType nvarchar(32));
    """, d)
    sa_exec(SRC_PORT, "CREATE VIEW dbo.ExecutionLog3 AS SELECT * FROM dbo.ExecutionLogStore", d)
    try:
        import pymssql as _p
        conn = _p.connect(server=HOST, port=str(SRC_PORT), user="sa", password=PASSWORD, database=d, autocommit=True)
        cur = conn.cursor()
        ids = {}
        for rdl in sorted((EXAMPLES / "rdl").rglob("*.rdl")):
            rid = str(uuid.uuid4())
            path = "/" + "/".join(rdl.relative_to(EXAMPLES / "rdl").with_suffix("").parts)
            ids[path] = rid
            cur.execute("INSERT INTO dbo.Catalog VALUES (%s, %s, %s, 2, NULL, CAST(%s AS varbinary(max)))", (rid, path, rdl.stem, rdl.read_bytes()))
        # a linked report pointing at SalesByTerritory
        cur.execute("INSERT INTO dbo.Catalog VALUES (%s, %s, %s, 4, %s, NULL)", (str(uuid.uuid4()), "/Sales/SalesByTerritoryEU", "SalesByTerritoryEU", ids["/Sales/SalesByTerritory"]))
        cur.execute("INSERT INTO dbo.Subscriptions VALUES (%s, 'Report Server FileShare', '<DataSettings/>')", (ids["/Sales/MonthlyCommissions"],))
        with open(EXAMPLES / "usage.csv", newline="") as fh:
            log_rows = list(csv.DictReader(fh))
        for r in log_rows:
            cur.execute("INSERT INTO dbo.ExecutionLogStore VALUES (%s,%s,%s,%s,%s,%s)", (r["ItemPath"], r["UserName"], r["TimeStart"], r["TimeEnd"], r["Status"], r["RequestType"]))
        conn.close()

        with SafeConnection(src.with_database(d), QueryGuard()) as c:
            usage = usage_mod.load_executionlog(c)
            res = ssrs_mod.from_reportserver(c, usage, classify.load_config())
        assert usage.window_days >= 365 and usage.source == "executionlog3"
        by = {r["path"]: r for r in res["reports"]}
        assert res["report_count"] == 13                                                  # 12 reports + 1 linked report
        assert by["/Sales/SalesByTerritoryEU"]["linked_report_of"] == "/Sales/SalesByTerritory"
        assert by["/Inventory/LegacyInventory"]["classification"] == "retire"
        assert by["/Sales/OldPriceList"]["classification"] == "retire"
        assert by["/Sales/MonthlyCommissions"]["subscriptions"] == 1 and by["/Sales/MonthlyCommissions"]["classification"] == "review"
        assert by["/Sales/SalesByTerritory"]["runs_365"] > 100 and by["/Sales/SalesByTerritory"]["distinct_users"] > 1
        assert by["/Sales/CustomerOrders"]["subreports"] == ["OrderDetails"]
    finally:
        sa_exec(SRC_PORT, "ALTER DATABASE ReportServerStandIn SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE IF EXISTS ReportServerStandIn;")
