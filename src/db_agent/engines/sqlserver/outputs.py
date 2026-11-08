"""Write results to the output folder: Markdown, JSON, CSV, and tabs of one Excel workbook."""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import END_OF_SUPPORT
from .inventory import major_version

WORKBOOK = "db-agent-sqlserver.xlsx"
FORMATS = ("md", "json", "xlsx", "html")


def _default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat(sep=" ") if isinstance(o, datetime) else o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (bytes, bytearray)):
        return f"<{len(o)} bytes>"
    return str(o)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=_default, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c)) for c in columns})


def _cell(v: Any) -> Any:
    if isinstance(v, (list, tuple, set)):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, default=_default)
    if isinstance(v, bool):
        return "yes" if v else "no"
    return "" if v is None else v


def update_sheet(out: Path, sheet: str, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Create or replace one tab of the shared workbook."""
    path = out / WORKBOOK
    wb = load_workbook(path) if path.exists() else Workbook()
    if sheet in wb.sheetnames:
        del wb[sheet]
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) == 1:
        del wb["Sheet"]
    ws = wb.create_sheet(sheet)
    ws.append(columns)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for r in rows:
        ws.append([_xl(_cell(r.get(c))) for c in columns])
    for i, col in enumerate(columns, start=1):
        width = max([len(str(col))] + [len(str(_cell(r.get(col)))) for r in rows[:200]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 70)
    ws.freeze_panes = "A2"
    wb.save(path)


def _xl(v: Any) -> Any:
    if isinstance(v, str) and v and v[0] in "=+-@":
        return "'" + v  # stop Excel treating text as a formula
    return v


def order_workbook(out: Path) -> None:
    path = out / WORKBOOK
    if not path.exists():
        return
    wb = load_workbook(path)
    order = ["Summary", "Reports", "Risks", "Inventory", "Validation"]
    wb._sheets = sorted(wb._sheets, key=lambda s: order.index(s.title) if s.title in order else 99)
    wb.save(path)


def eos_info(product_version: str, today: date | None = None) -> dict[str, Any]:
    major = major_version(product_version)
    today = today or date.today()
    if major in END_OF_SUPPORT:
        name, d = END_OF_SUPPORT[major]
        eos = date.fromisoformat(d)
        days = (eos - today).days
        return {"name": name, "date": d, "days": days,
                "text": f"{name} end of support: {d} ({'ended ' + str(-days) + ' days ago' if days < 0 else str(days) + ' days left'})"}
    return {"name": f"SQL Server {product_version}", "date": None, "days": None,
            "text": "End-of-support date not in this kit's table. Check Microsoft's lifecycle page."}


def safe_filename(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/")) or "report"


# ---- inventory ------------------------------------------------------------------------------
def inventory_rows(inv: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for dbname, db in inv["objects"].items():
        for t in db["tables"]:
            rows.append({"database": dbname, "kind": "table", "schema": t["schema"], "name": t["name"], "rows": t["rows"],
                         "columns": len(t["columns"]), "indexes": len(t["indexes"]), "detail": ""})
        for v in db["views"]:
            rows.append({"database": dbname, "kind": "view", "schema": v["schema"], "name": v["name"], "rows": None,
                         "columns": len(v["columns"]), "indexes": None, "detail": ""})
        for m in db["modules"]:
            rows.append({"database": dbname, "kind": m["type"].lower(), "schema": m["schema"], "name": m["name"], "rows": None,
                         "columns": None, "indexes": None, "detail": "encrypted" if m["encrypted"] else ""})
    return rows


def render_inventory_md(inv: dict[str, Any], risks: list[dict[str, Any]]) -> str:
    s = inv["server"]
    eos = eos_info(s["product_version"])
    L = ["# SQL Server inventory", "",
         f"- Server: {s['server_name']}",
         f"- Version: {s['product_version']} {s['product_level']}, {s['edition']}",
         f"- Collation: {s['collation']}", f"- {eos['text']}", ""]
    L += ["## Databases", "", "| Database | Compat level | Data MB | Log MB | State |", "|---|---|---|---|---|"]
    for d in inv["databases"]:
        L.append(f"| {d['name']} | {d['compatibility_level']} | {d['data_mb']} | {d['log_mb']} | {d['state_desc']} |")
    L += ["", "## Objects per database", "", "| Database | Tables | Rows (metadata) | Views | Procedures, functions, triggers |", "|---|---|---|---|---|"]
    for name, db in inv["objects"].items():
        total = sum((t["rows"] or 0) for t in db["tables"])
        L.append(f"| {name} | {len(db['tables'])} | {total:,} | {len(db['views'])} | {len(db['modules'])} |")
    L += ["", f"Linked servers: {len(inv.get('linked_servers', []))}. SQL Server Agent jobs: {len({j['job_name'] for j in inv.get('agent_jobs', [])})}.", ""]
    by_sev: dict[str, int] = {}
    for r in risks:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
    L += ["## Upgrade risks", "", ", ".join(f"{k}: {v}" for k, v in sorted(by_sev.items())) or "None found.", ""]
    if risks:
        L += ["| Severity | Database | Object | Finding | Source |", "|---|---|---|---|---|"]
        for r in risks[:60]:
            L.append(f"| {r['severity']} | {r['database']} | {r['object']} | {r['title']}. {r['detail'][:140]} | [doc]({r['doc']}) |")
        if len(risks) > 60:
            L.append(f"\n{len(risks) - 60} more in risks.csv.")
    if inv["warnings"]:
        L += ["", "## Not collected", ""] + [f"- {w}" for w in inv["warnings"]]
    return "\n".join(L) + "\n"


RISK_COLUMNS = ["severity", "category", "database", "object", "title", "detail", "azure_sql_database_only", "doc"]
INV_COLUMNS = ["database", "kind", "schema", "name", "rows", "columns", "indexes", "detail"]


def write_inventory(out: Path, inv: dict[str, Any], risks: list[dict[str, Any]], formats: set[str], include_code: bool) -> list[str]:
    written = []
    modules = {db: [{"schema": m["schema"], "name": m["name"], "type": m["type"], "definition": m["definition"]} for m in o["modules"]]
               for db, o in inv["objects"].items()}
    slim = json.loads(json.dumps(inv, default=_default))
    for o in slim["objects"].values():
        for m in o["modules"]:
            m.pop("definition", None)
    slim.pop("agent_jobs", None)
    slim["agent_jobs"] = [{k: v for k, v in j.items() if k != "command"} for j in inv.get("agent_jobs", [])]
    slim["linked_servers"] = [{k: v for k, v in ls.items() if k != "data_source"} for ls in inv.get("linked_servers", [])]
    if "json" in formats:
        write_json(out / "inventory.json", slim)
        written.append("inventory.json")
        if include_code:
            write_json(out / "modules.json", modules)
            written.append("modules.json (object code; may contain secrets, handle with care)")
    if "md" in formats:
        (out / "inventory.md").write_text(render_inventory_md(inv, risks), encoding="utf-8")
        write_csv(out / "risks.csv", risks, RISK_COLUMNS)
        written += ["inventory.md", "risks.csv"]
    if "xlsx" in formats:
        update_sheet(out, "Inventory", inventory_rows(inv), INV_COLUMNS)
        update_sheet(out, "Risks", risks, RISK_COLUMNS)
        written.append(f"{WORKBOOK} (Inventory, Risks)")
    write_json(out / "risks.json", risks)  # read by `report`
    return written


# ---- ssrs -----------------------------------------------------------------------------------
REPORT_COLUMNS = ["path", "name", "folder", "classification", "reasons", "layout_hint", "last_run", "runs_90", "runs_365",
                  "distinct_users", "avg_ms", "errors", "dataset_count", "parameter_count", "tables", "procedures", "ref_confidence",
                  "data_source_servers", "subreports", "drillthrough", "custom_code", "custom_assemblies", "subscriptions",
                  "expressions", "complex_expressions", "embedded_credentials", "rdl_version", "linked_report_of", "parse_error"]


def render_ssrs_summary_md(res: dict[str, Any]) -> str:
    c = res["classification_counts"]
    u = res["usage"]
    reps = res["reports"]
    used = sum(1 for r in reps if (r["runs_365"] or 0) > 0)
    L = ["# SSRS report inventory: summary", "",
         f"Reports found: **{res['report_count']}**  |  Move: **{c.get('move', 0)}**  |  Review: **{c.get('review', 0)}**  |  Retire: **{c.get('retire', 0)}**", ""]
    if u["source"]:
        L += [f"Usage source: {u['source']}. The log reaches back {u['log_window_days']} days (oldest entry {u['oldest']}).",
              f"Reports run in the last 365 days: {used}. Not run: {res['report_count'] - used}.", ""]
        if (u["log_window_days"] or 0) < 365:
            L += ["> **The usage log is shorter than a year.** SSRS keeps 60 days of log by default. A report that runs once a quarter or once a year can look unused. "
                  "No report is called retire until the log covers 365 days.", ""]
    else:
        L += ["No usage data was supplied, so no report is called retire. Supply the execution log (--reportserver-db) or a CSV (--usage-csv).", ""]
    if not res["subscriptions_known"]:
        L += ["Subscriptions were not read (folder mode without subscriptions.csv).", ""]
    L += ["## What to do first", "",
          f"1. **Retire ({c.get('retire', 0)}).** Confirm with the owners, then leave these out of the move.",
          f"2. **Move ({c.get('move', 0)}).** Plain reports. Start here; check the results with `validate`.",
          f"3. **Review ({c.get('review', 0)}).** Each has a reason below. Someone needs to look at it before it is moved.", ""]
    for label in ("retire", "review", "move"):
        group = [r for r in reps if r["classification"] == label]
        if not group:
            continue
        L += [f"## {label.capitalize()} ({len(group)})", "", "| Report | Runs (365 days) | Why |", "|---|---|---|"]
        for r in group:
            L.append(f"| {r['path']} | {r['runs_365'] if r['runs_365'] is not None else 'n/a'} | {'; '.join(r['reasons'])} |")
        L.append("")
    if res["table_usage"]:
        L += ["## Tables used most by reports", "", "| Table | Reports |", "|---|---|"] + [f"| {t} | {n} |" for t, n in res["table_usage"][:15]] + [""]
    L += ["Table lists come from parsing report SQL and are best effort. See the confidence column in reports.csv.", ""]
    return "\n".join(L)


def render_report_detail_md(r: dict[str, Any]) -> str:
    L = [f"# {r['path']}", "",
         f"- Classification: **{r['classification']}**. {'; '.join(r['reasons'])}",
         f"- Layout hint: {r['layout_hint']} (a hint only)",
         f"- RDL version: {r['rdl_version']}",
         f"- Runs: {r['runs_90']} in 90 days, {r['runs_365']} in 365 days, last run {r['last_run']}, distinct users {r['distinct_users']}"
         if r["usage_known"] else "- Usage: not supplied", ""]
    if r["data_sources"]:
        L += ["## Data sources", ""]
        for s in r["data_sources"]:
            L.append(f"- {s['name']}: {s['provider'] or (s['shared_reference'] and 'shared ' + s['shared_reference']) or '?'}, server {s['server']}, database {s['database']}"
                     + (" (**connection string contains a password**)" if s["embedded_credentials"] else ""))
        L.append("")
    if r["parameters"]:
        L += ["## Parameters", "", "| Name | Type | Default | Multi-value |", "|---|---|---|---|"]
        L += [f"| {p['name']} | {p['type']} | {p['default']} | {p['multi_value']} |" for p in r["parameters"]]
        L.append("")
    for d in r["datasets"]:
        L += [f"## Dataset {d['name']}", "", f"- Command type: {d['command_type']}. Reference confidence: {d['confidence']}"
              + (f" ({d['parse_error']})" if d["parse_error"] else ""),
              f"- Tables: {', '.join(d['tables']) or 'none found'}", f"- Procedures: {', '.join(d['procedures']) or 'none'}", "", "```sql", d["command_text"].strip(), "```", ""]
    if r["subreports"]:
        L += ["Subreports: " + ", ".join(r["subreports"]), ""]
    return "\n".join(L)


def write_ssrs(out: Path, res: dict[str, Any], formats: set[str]) -> list[str]:
    written = []
    write_json(out / "reports.json", res)  # read by validate and report
    written.append("reports.json")
    if "md" in formats:
        write_csv(out / "reports.csv", res["reports"], REPORT_COLUMNS)
        (out / "summary.md").write_text(render_ssrs_summary_md(res), encoding="utf-8")
        d = out / "report-details"
        d.mkdir(exist_ok=True)
        for r in res["reports"]:
            (d / f"{safe_filename(r['path'])}.md").write_text(render_report_detail_md(r), encoding="utf-8")
        written += ["reports.csv", "summary.md", f"report-details/ ({len(res['reports'])} files)"]
    if "xlsx" in formats:
        update_sheet(out, "Reports", res["reports"], REPORT_COLUMNS)
        written.append(f"{WORKBOOK} (Reports)")
    return written


# ---- validate -------------------------------------------------------------------------------
VALIDATION_COLUMNS = ["report", "dataset", "status", "reason", "source_rows", "target_rows", "source_ms", "target_ms", "differences"]


def write_validation(out: Path, rows: list[dict[str, Any]], formats: set[str]) -> list[str]:
    write_json(out / "validation.json", rows)
    written = ["validation.json"]
    if "md" in formats:
        write_csv(out / "validation.csv", rows, VALIDATION_COLUMNS)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        L = ["# Validation", "", "Compared on row counts and totals of numeric columns. No row values are stored.", "",
             ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "Nothing was compared.", "",
             "| Report | Dataset | Status | Source rows | Target rows | Note |", "|---|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['report']} | {r['dataset']} | {r['status']} | {r['source_rows'] if r['source_rows'] is not None else ''} | "
                     f"{r['target_rows'] if r['target_rows'] is not None else ''} | {(r['differences'] or r['reason'])[:160]} |")
        (out / "validation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        written += ["validation.csv", "validation.md"]
    if "xlsx" in formats:
        update_sheet(out, "Validation", rows, VALIDATION_COLUMNS)
        written.append(f"{WORKBOOK} (Validation)")
    return written
