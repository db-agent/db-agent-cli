"""Find the tables, views and procedures a dataset query uses. Best effort, with a confidence flag.

high   : sqlglot parsed every statement.
medium : sqlglot parsed some statements; the rest were read with a text fallback.
low    : sqlglot failed; names come from a text fallback only.
n/a    : nothing to parse (empty, or a stored procedure call, or a non-SQL language).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .guard import sanitize

_FALLBACK_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|MERGE\s+INTO)\s+((?:\[[^\]]+\]|[\w#@$]+)(?:\s*\.\s*(?:\[[^\]]+\]|[\w#@$]+)){0,3})",
    re.IGNORECASE,
)
_EXEC_RE = re.compile(r"\bEXEC(?:UTE)?\s+((?:\[[^\]]+\]|[\w$]+)(?:\.(?:\[[^\]]+\]|[\w$]+)){0,2})", re.IGNORECASE)
_SQL_KEYWORDS = {"select", "values", "(", "lateral", "openquery", "openrowset"}


@dataclass
class Refs:
    tables: list[str] = field(default_factory=list)  # tables and views (cannot tell which from text)
    procedures: list[str] = field(default_factory=list)
    temp_tables: list[str] = field(default_factory=list)
    confidence: str = "n/a"
    error: str | None = None


def _clean_name(name: str) -> str:
    return re.sub(r"\s*\.\s*", ".", name.replace("[", "").replace("]", "")).strip()


def _fallback(sql: str) -> tuple[list[str], list[str]]:
    clean = sanitize(sql, preserve_length=False)
    tables = [m.group(1) for m in _FALLBACK_RE.finditer(sql)]
    procs = [m.group(1) for m in _EXEC_RE.finditer(clean)]
    return tables, procs


def extract(sql: str, command_type: str = "Text") -> Refs:
    refs = Refs()
    text = (sql or "").strip()
    if not text:
        return refs
    if command_type == "StoredProcedure":
        refs.procedures = [_clean_name(text)]
        refs.confidence = "high"
        return refs
    if text.startswith("="):
        refs.confidence = "n/a"
        refs.error = "query is an expression, not static SQL"
        return refs
    tables: set[str] = set()
    temps: set[str] = set()
    procs: set[str] = set()
    try:
        trees = [t for t in sqlglot.parse(text, read="tsql") if t is not None]
    except Exception as exc:  # noqa: BLE001
        t_fallback, p_fallback = _fallback(text)
        refs.error = f"parse failed: {str(exc).splitlines()[0][:140]}"
        refs.confidence = "low"
        for n in t_fallback:
            (temps if _clean_name(n).startswith("#") else tables).add(_clean_name(n))
        procs.update(_clean_name(p) for p in p_fallback)
    else:
        degraded = False
        for tree in trees:
            if isinstance(tree, exp.Command):
                degraded = True
                t_fallback, p_fallback = _fallback(tree.sql(dialect="tsql"))
                for n in t_fallback:
                    (temps if _clean_name(n).startswith("#") else tables).add(_clean_name(n))
                procs.update(_clean_name(p) for p in p_fallback)
                continue
            ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
            exec_targets = set()
            for ex in tree.find_all(exp.Execute):
                if isinstance(ex.this, exp.Table):
                    exec_targets.add(id(ex.this))
                    procs.add(".".join(p for p in (ex.this.catalog, ex.this.db, ex.this.name) if p))
            for t in tree.find_all(exp.Table):
                if not t.name or id(t) in exec_targets:
                    continue
                if not t.args.get("db") and t.name.lower() in ctes:
                    continue
                parts = [p for p in (t.catalog, t.db, t.name) if p]
                is_temp = isinstance(t.this, exp.Identifier) and bool(t.this.args.get("temporary"))
                if is_temp:
                    temps.add("#" + t.name)
                else:
                    tables.add(".".join(parts))
        refs.confidence = "medium" if degraded else "high"
    refs.tables = sorted(tables, key=str.lower)
    refs.temp_tables = sorted(temps)
    refs.procedures = sorted(procs, key=str.lower)
    return refs
