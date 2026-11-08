from typer.testing import CliRunner

from dbagent import __version__
from dbagent.cli import app
from dbagent.engines import ENGINES

runner = CliRunner()


def test_engines_lists_available_and_planned():
    out = runner.invoke(app, ["engines"]).output
    assert "sqlserver" in out and "available" in out
    for name in ("postgres", "mysql", "databricks"):
        assert name in out and "planned" in out


def test_sqlserver_verbs_are_mounted_under_the_engine():
    out = runner.invoke(app, ["sqlserver", "--help"]).output
    for verb in ("inventory", "ssrs", "validate", "report"):
        assert verb in out


def test_planned_engines_are_not_commands():
    assert runner.invoke(app, ["postgres"]).exit_code != 0


def test_only_available_engines_have_a_module():
    for e in ENGINES:
        assert (e.status == "available") == bool(e.module)


def test_single_version():
    import dbagent.engines.sqlserver as s
    assert s.__version__ == __version__
