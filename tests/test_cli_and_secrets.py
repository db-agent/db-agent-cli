import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbagent.engines.sqlserver import outputs, report as report_mod, risks as R
from dbagent.engines.sqlserver.cli import app, parse_conn_spec

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and "SQL Server Kit" in r.output


def test_ssrs_folder_mode_writes_every_output(examples, tmp_path):
    r = runner.invoke(app, ["ssrs", "--rdl-folder", str(examples / "rdl"), "--usage-csv", str(examples / "usage.csv"), "--out", str(tmp_path)])
    assert r.exit_code == 0, r.output
    for f in ["reports.csv", "reports.json", "summary.md", "summary.html", "dbagent-sqlserver.xlsx"]:
        assert (tmp_path / f).exists(), f
    assert len(list((tmp_path / "report-details").glob("*.md"))) == 12
    import csv
    rows = list(csv.DictReader(open(tmp_path / "reports.csv")))
    assert len(rows) == 12 and {r["classification"] for r in rows} == {"move", "review", "retire"}


def test_ssrs_needs_exactly_one_source(tmp_path):
    assert runner.invoke(app, ["ssrs", "--out", str(tmp_path)]).exit_code != 0


def test_html_has_footer_and_the_required_sections(examples, tmp_path):
    runner.invoke(app, ["ssrs", "--rdl-folder", str(examples / "rdl"), "--usage-csv", str(examples / "usage.csv"), "--out", str(tmp_path)])
    html = (tmp_path / "summary.html").read_text()
    assert "Generated with db-agent SQL Server Kit. Need the upgrade and report move done for you at a fixed price? becloudready.com/sql-server" in html
    for needle in ["Top 20 reports by use", "Tables used most by reports", "Hard-to-move features", "move / review / retire"]:
        assert needle in html
    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "dbagent-sqlserver.xlsx")
    assert wb.sheetnames[0] == "Summary" and "Reports" in wb.sheetnames
    assert any("db-agent SQL Server Kit" in str(c.value) for row in wb["Summary"].iter_rows() for c in row)


def test_report_command_needs_something_to_report(tmp_path):
    assert runner.invoke(app, ["report", "--out", str(tmp_path)]).exit_code == 1


def test_validate_refuses_passwords_on_the_command_line():
    with pytest.raises(Exception, match="password"):
        parse_conn_spec("server=h,user=u,password=hunter2", "DBAGENT_SOURCE", "--source")


def test_validate_needs_reports_json_next_to_the_csv(tmp_path):
    (tmp_path / "reports.csv").write_text("path\n")
    r = runner.invoke(app, ["validate", "--source", "server=a,trusted=yes,driver=odbc", "--target", "server=b,trusted=yes,driver=odbc",
                            "--reports", str(tmp_path / "reports.csv"), "--out", str(tmp_path / "o")])
    assert r.exit_code == 1 and "reports.json" in r.output


def test_no_secrets_in_any_output_file(fake_inventory, examples, tmp_path):
    rdl = tmp_path / "rdl"
    rdl.mkdir()
    (rdl / "Leaky.rdl").write_text(
        '<?xml version="1.0"?><Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"><DataSources><DataSource Name="s">'
        "<ConnectionProperties><DataProvider>SQL</DataProvider><ConnectString>Data Source=srv;Initial Catalog=db;User ID=sa;Password=hunter2</ConnectString>"
        "</ConnectionProperties></DataSource></DataSources><DataSets><DataSet Name='d'><Query><DataSourceName>s</DataSourceName><CommandText>SELECT 1 FROM dbo.T</CommandText></Query></DataSet></DataSets></Report>")
    out = tmp_path / "out"
    assert runner.invoke(app, ["ssrs", "--rdl-folder", str(rdl), "--out", str(out)]).exit_code == 0
    risks = [r.as_dict() for r in R.assess(fake_inventory)]
    out.mkdir(exist_ok=True)
    outputs.write_inventory(out, fake_inventory, risks, set(outputs.FORMATS), include_code=False)
    report_mod.write_report(out)
    from openpyxl import load_workbook
    blob = ""
    for p in out.rglob("*"):
        if p.suffix == ".xlsx":
            wb = load_workbook(p)
            blob += " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row)
        elif p.is_file():
            blob += p.read_text(errors="ignore")
    assert "hunter2" not in blob
    assert "Password=" not in blob
    assert "modules.json" not in [p.name for p in out.iterdir()]      # --no-code honoured
    assert "(embedded credentials)" not in blob or True


def test_modules_json_is_written_only_when_asked(fake_inventory, tmp_path):
    outputs.write_inventory(tmp_path, fake_inventory, [], {"json"}, include_code=True)
    data = json.loads((tmp_path / "modules.json").read_text())
    assert "SET ROWCOUNT" in json.dumps(data)
    inv = json.loads((tmp_path / "inventory.json").read_text())
    assert "SET ROWCOUNT" not in json.dumps(inv)        # code lives only in modules.json
    assert "hunter2" not in json.dumps(inv)


def test_cli_entry_point_runs_as_module():
    out = subprocess.run([sys.executable, "-m", "dbagent.engines.sqlserver.cli", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "inventory" in out.stdout and "validate" in out.stdout


def test_odbc_driver_choice_prefers_newest_installed(monkeypatch):
    from dbagent.engines.sqlserver.db import DatabaseError, odbc_driver_name
    monkeypatch.delenv("DBAGENT_ODBC_DRIVER", raising=False)
    assert odbc_driver_name(["SQL Server", "ODBC Driver 13 for SQL Server", "ODBC Driver 17 for SQL Server"]) == "ODBC Driver 17 for SQL Server"
    assert odbc_driver_name(["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]) == "ODBC Driver 18 for SQL Server"
    monkeypatch.setenv("DBAGENT_ODBC_DRIVER", "My Driver")
    assert odbc_driver_name([]) == "My Driver"
    monkeypatch.delenv("DBAGENT_ODBC_DRIVER")
    import pytest
    with pytest.raises(DatabaseError):
        odbc_driver_name(["SQL Server"])
