from pathlib import Path

from dbagent.engines.sqlserver import rdl, sqlrefs


def parse(examples, rel):
    return rdl.parse_rdl_file(examples / "rdl" / rel, examples / "rdl")


def test_basic_report(examples):
    r = parse(examples, "Sales/SalesByTerritory.rdl")
    assert r.path == "/Sales/SalesByTerritory" and r.rdl_version == "2016"
    assert [p["name"] for p in r.parameters] == ["StartDate", "EndDate"]
    assert r.data_sources[0].server == "sql2017.example.local" and r.data_sources[0].database == "AdventureWorks2017"
    assert r.datasets[0].query_parameters[0]["name"] == "@StartDate"
    assert rdl.layout_hint(r) == "paginated"


def test_subreport_code_chart_and_proc(examples):
    assert parse(examples, "Sales/CustomerOrders.rdl").subreports == ["OrderDetails"]
    code = parse(examples, "HR/EmployeeDirectory.rdl")
    assert code.has_custom_code and code.custom_code_lines == 3 and code.complex_expressions == 1
    assert rdl.layout_hint(parse(examples, "Sales/TopProducts.rdl")) == "interactive"
    bom = parse(examples, "Inventory/BillOfMaterials.rdl")
    assert bom.datasets[0].command_type == "StoredProcedure" and bom.datasets[0].command_text == "dbo.uspGetBillOfMaterials"
    assert parse(examples, "Inventory/ProductCatalog.rdl").parameters[0]["multi_value"] is True


def test_every_namespace_version_is_read():
    for ns, label in rdl.RDL_NAMESPACES.items():
        xml = f'<?xml version="1.0"?><Report xmlns="{ns}"><DataSets><DataSet Name="d"><Query><DataSourceName>s</DataSourceName><CommandText>SELECT 1</CommandText></Query></DataSet></DataSets></Report>'
        r = rdl.parse_rdl_bytes(xml.encode(), "x.rdl")
        assert r.rdl_version == label and r.datasets[0].command_text == "SELECT 1"


def test_bad_xml_and_wrong_root_are_reported_not_raised():
    assert "invalid XML" in rdl.parse_rdl_bytes(b"<Report><oops>", "a.rdl").parse_error
    assert "not a report" in rdl.parse_rdl_bytes(b"<Other/>", "b.rdl").parse_error


def test_xml_external_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    xml = f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]><Report xmlns="{list(rdl.RDL_NAMESPACES)[0]}"><Code>&x;</Code></Report>'
    r = rdl.parse_rdl_bytes(xml.encode(), "x.rdl")
    assert "TOP-SECRET" not in repr(r)


def test_connection_string_keeps_server_and_database_but_never_the_password():
    xml = ('<?xml version="1.0"?><Report xmlns="%s"><DataSources><DataSource Name="s"><ConnectionProperties><DataProvider>SQL</DataProvider>'
           "<ConnectString>Data Source=srv1;Initial Catalog=Db1;User ID=sa;Password=hunter2;</ConnectString></ConnectionProperties></DataSource></DataSources></Report>") % list(rdl.RDL_NAMESPACES)[0]
    r = rdl.parse_rdl_bytes(xml.encode(), "x.rdl")
    s = r.data_sources[0]
    assert (s.server, s.database, s.embedded_credentials) == ("srv1", "Db1", True)
    assert "hunter2" not in repr(r)


def test_sql_refs_high_confidence():
    r = sqlrefs.extract("SELECT a FROM Sales.Orders o JOIN [dbo].[Cust] c ON c.id = o.cid WHERE o.d > @d")
    assert r.confidence == "high" and r.tables == ["dbo.Cust", "Sales.Orders"]


def test_sql_refs_ignores_ctes_and_flags_temp_tables():
    r = sqlrefs.extract("WITH x AS (SELECT * FROM dbo.A) SELECT * FROM x JOIN #tmp t ON 1=1")
    assert r.tables == ["dbo.A"] and r.temp_tables == ["#tmp"]


def test_sql_refs_stored_procedure_and_expression():
    assert sqlrefs.extract("dbo.uspX", "StoredProcedure").procedures == ["dbo.uspX"]
    e = sqlrefs.extract('="SELECT * FROM t WHERE x=" & Parameters!p.Value')
    assert e.confidence == "n/a" and e.tables == []


def test_sql_refs_falls_back_with_low_confidence_when_unparseable():
    r = sqlrefs.extract("SELECT 1 FROM Purchasing.Vendor v, Purchasing.PurchaseOrderHeader po WHERE v.id *= po.id")
    assert r.confidence in ("low", "medium") and "Purchasing.Vendor" in r.tables


def test_sql_refs_exec_in_text():
    r = sqlrefs.extract("EXEC dbo.uspDoThing @a = 1")
    assert "dbo.uspDoThing" in r.procedures
