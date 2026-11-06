"""Enforces that nothing can bypass the query guard from a command."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dbagent_sqlserver"


def test_only_db_module_imports_a_driver():
    offenders = []
    for p in SRC.glob("*.py"):
        if p.name == "db.py":
            continue
        text = p.read_text()
        if re.search(r"^\s*(import|from)\s+(pymssql|pyodbc)\b", text, re.MULTILINE):
            offenders.append(p.name)
    assert not offenders, offenders


def test_no_module_touches_the_raw_connection_or_cursor():
    offenders = []
    for p in SRC.glob("*.py"):
        if p.name == "db.py":
            continue
        text = p.read_text()
        if re.search(r"\.conn\b|\._conn\b|\.cursor\(", text):
            offenders.append(p.name)
    assert not offenders, offenders


def test_guard_runs_before_execute_in_db_module():
    text = (SRC / "db.py").read_text()
    body = text[text.index("def _cursor") : text.index("def query")]
    assert body.index("self.guard.check(sql)") < body.index(".execute(")


def test_no_network_libraries_are_imported():
    for p in SRC.glob("*.py"):
        text = p.read_text()
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp)\b", text, re.MULTILINE), p.name
