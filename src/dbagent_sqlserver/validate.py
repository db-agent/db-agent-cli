"""Compare report dataset results between a source and a target server.

Rows are folded into a row count and per-column totals as they arrive and are then discarded.
Only counts and totals reach memory beyond one chunk, and only counts and totals reach disk.
Every statement passes through the QueryGuard. Stored procedures run only with allow_exec.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from .db import ConnectionConfig, DatabaseError, SafeConnection
from .guard import GuardError, QueryGuard, sanitize

_PARAM_RE = re.compile(r"(?<![\w@])@(\w+)")


@dataclass
class Fold:
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    sums: dict[str, Decimal | float] = field(default_factory=dict)
    nonnull: dict[str, int] = field(default_factory=dict)
    ms: int = 0
    truncated: bool = False


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def fold_chunks(chunks: Any, max_rows: int) -> Fold:
    out = Fold()
    started = time.monotonic()
    for cols, rows in chunks:
        if not out.columns:
            out.columns = list(cols)
        for row in rows:
            out.rows += 1
            for c, v in zip(cols, row):
                if v is None:
                    continue
                out.nonnull[c] = out.nonnull.get(c, 0) + 1
                if _is_number(v):
                    cur = out.sums.get(c, Decimal(0) if not isinstance(v, float) else 0.0)
                    if isinstance(cur, Decimal) and isinstance(v, float):
                        cur = float(cur)
                    elif isinstance(cur, float) and isinstance(v, Decimal):
                        v = float(v)
                    out.sums[c] = cur + v
        if out.rows > max_rows:
            out.truncated = True
            break
    out.ms = int((time.monotonic() - started) * 1000)
    return out


def compare(src: Fold, tgt: Fold, rel_tol: float = 1e-9) -> tuple[str, list[str]]:
    """Return ("pass" | "fail", differences). Differences hold counts and totals only."""
    diffs: list[str] = []
    s_cols, t_cols = [c.lower() for c in src.columns], [c.lower() for c in tgt.columns]
    if s_cols != t_cols:
        diffs.append(f"columns differ: source {src.columns} target {tgt.columns}")
    if src.rows != tgt.rows:
        diffs.append(f"row count: source {src.rows}, target {tgt.rows}")
    for c in src.columns:
        a, b = src.sums.get(c), tgt.sums.get(c)
        if a is None and b is None:
            continue
        if a is None or b is None:
            diffs.append(f"{c}: numeric total present on one side only")
            continue
        if isinstance(a, float) or isinstance(b, float):
            ok = math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=1e-9)
        else:
            ok = a == b
        if not ok:
            diffs.append(f"{c} total: source {a}, target {b}")
        if src.nonnull.get(c, 0) != tgt.nonnull.get(c, 0):
            diffs.append(f"{c} non-null count: source {src.nonnull.get(c, 0)}, target {tgt.nonnull.get(c, 0)}")
    return ("fail" if diffs else "pass"), diffs


def coerce(value: str, dtype: str | None) -> Any:
    t = (dtype or "String").lower()
    if t in ("integer", "int", "int32", "int64"):
        return int(value)
    if t in ("float", "decimal", "double"):
        return float(value)
    if t == "boolean":
        return value.strip().lower() in ("true", "1", "yes")
    if t == "datetime":
        s = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"bad date value {value!r}")
    return value


def bind_params(report: dict[str, Any], dataset: dict[str, Any], overrides: dict[str, Any]) -> tuple[str, dict[str, Any]] | str:
    """Return (sql_with_placeholders, params) or a string explaining why the dataset must be skipped."""
    rp = {p["name"].lower(): p for p in report["parameters"]}
    sql = dataset["command_text"]
    wanted = {m.group(1) for m in _PARAM_RE.finditer(sanitize(sql, preserve_length=True))}
    mapping: dict[str, str] = {}  # sql parameter -> report parameter
    for q in dataset["query_parameters"]:
        m = re.fullmatch(r"=\s*Parameters!(\w+)\.Value", q["value"].strip(), re.IGNORECASE)
        mapping[q["name"].lstrip("@").lower()] = m.group(1) if m else q["value"]
    params: dict[str, Any] = {}
    for name in wanted:
        source = mapping.get(name.lower(), name)
        p = rp.get(source.lower())
        if p and p["multi_value"]:
            return f"parameter {source} is multi-value; not supported in this version"
        raw = overrides.get(source) if source in overrides else overrides.get(name)
        if raw is None and p:
            raw = p["default"]
        if raw is None or (isinstance(raw, str) and raw.startswith("=")):
            return f"no value for parameter {source}; set it in params.yaml"
        try:
            params[name] = coerce(str(raw), p["type"] if p else None)
        except ValueError as exc:
            return f"parameter {source}: {exc}"
    if not params:
        return sql, {}
    text = sanitize(sql, preserve_length=True)
    out, last = [], 0
    for m in _PARAM_RE.finditer(text):
        out.append(sql[last : m.start()].replace("%", "%%"))
        out.append(f"%({m.group(1)})s")
        last = m.end()
    out.append(sql[last:].replace("%", "%%"))
    return "".join(out), params


def _run(cfg: ConnectionConfig, guard: QueryGuard, sql: str, params: dict[str, Any] | None, max_rows: int) -> Fold:
    with SafeConnection(cfg, guard) as c:
        return fold_chunks(c.stream(sql, params or None), max_rows)


def validate_reports(
    reports: list[dict[str, Any]],
    source: ConnectionConfig,
    target: ConnectionConfig,
    overrides: dict[str, Any] | None = None,
    allow_exec: bool = False,
    max_rows: int = 1_000_000,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    overrides = overrides or {}
    say = progress or (lambda _m: None)
    results: list[dict[str, Any]] = []
    for rep in reports:
        if rep["classification"] != "move":
            continue
        rep_over = {**overrides.get("defaults", {}), **overrides.get("reports", {}).get(rep["path"], {})}
        for ds in rep["datasets"]:
            row = {"report": rep["path"], "dataset": ds["name"], "status": "skipped", "reason": "", "source_rows": None,
                   "target_rows": None, "source_ms": None, "target_ms": None, "differences": ""}
            results.append(row)
            say(f"{rep['path']} / {ds['name']}")
            if ds["shared_reference"] and not ds["command_text"]:
                row["reason"] = "shared dataset was not supplied"
                continue
            if ds["confidence"] == "n/a" and ds["command_type"] != "StoredProcedure":
                row["reason"] = ds.get("parse_error") or "nothing to run"
                continue
            if ds["command_type"] == "TableDirect":
                row["reason"] = "TableDirect datasets are not run"
                continue
            guard = QueryGuard()
            if ds["command_type"] == "StoredProcedure":
                if not allow_exec:
                    row["reason"] = "stored procedure; run again with --allow-exec"
                    continue
                proc = ds["command_text"].strip()
                guard.allow_proc(proc)
                args = ", ".join(f"@{q['name'].lstrip('@')}=%({q['name'].lstrip('@')})s" for q in ds["query_parameters"])
                bound = bind_exec(rep, ds, rep_over)
                if isinstance(bound, str):
                    row["reason"] = bound
                    continue
                sql, params = f"EXEC {proc} {args}".strip(), bound
            else:
                bound = bind_params(rep, ds, rep_over)
                if isinstance(bound, str):
                    row["reason"] = bound
                    continue
                sql, params = bound
            try:
                src = _run(source, guard, sql, params, max_rows)
            except GuardError as exc:
                row["reason"] = f"not run: cannot verify read-only ({exc})"
                continue
            except DatabaseError as exc:
                row["reason"] = f"source error: {str(exc)[:160]}"
                continue
            if src.truncated:
                row["reason"] = f"more than {max_rows} rows; not compared"
                continue
            try:
                tgt = _run(target, guard, sql, params, max_rows)
            except DatabaseError as exc:
                row.update(status="fail", reason=f"target error: {str(exc)[:160]}", source_rows=src.rows, source_ms=src.ms)
                continue
            status, diffs = compare(src, tgt)
            row.update(status=status, reason="" if status == "pass" else "results differ", source_rows=src.rows, target_rows=tgt.rows,
                       source_ms=src.ms, target_ms=tgt.ms, differences="; ".join(diffs))
    return results


def bind_exec(report: dict[str, Any], dataset: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any] | str:
    rp = {p["name"].lower(): p for p in report["parameters"]}
    params: dict[str, Any] = {}
    for q in dataset["query_parameters"]:
        name = q["name"].lstrip("@")
        m = re.fullmatch(r"=\s*Parameters!(\w+)\.Value", q["value"].strip(), re.IGNORECASE)
        source = m.group(1) if m else name
        p = rp.get(source.lower())
        raw = overrides.get(source, overrides.get(name))
        if raw is None and p:
            raw = p["default"]
        if raw is None or (isinstance(raw, str) and raw.startswith("=")):
            return f"no value for parameter {source}; set it in params.yaml"
        try:
            params[name] = coerce(str(raw), p["type"] if p else None)
        except ValueError as exc:
            return f"parameter {source}: {exc}"
    return params
