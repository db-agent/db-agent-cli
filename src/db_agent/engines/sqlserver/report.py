"""Combine inventory, ssrs and validate outputs into one client-ready summary.html and workbook."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from . import __version__
from .outputs import eos_info, order_workbook, update_sheet

FOOTER = "Generated with db-agent. Need the upgrade and report move done for you at a fixed price? becloudready.com/sql-server"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_summary(out: Path, today: date | None = None) -> dict[str, Any]:
    inv, risks = _load(out / "inventory.json"), _load(out / "risks.json") or []
    ssrs, val = _load(out / "reports.json"), _load(out / "validation.json")
    s: dict[str, Any] = {"generated": (today or date.today()).isoformat(), "version": __version__, "footer": FOOTER,
                         "have_inventory": inv is not None, "have_ssrs": ssrs is not None, "have_validation": val is not None}
    if inv:
        user_dbs = [d for d in inv["databases"] if d["name"] not in ("master", "model", "msdb", "tempdb")]
        s["server"] = inv["server"]
        s["eos"] = eos_info(inv["server"]["product_version"], today)
        s["db_count"] = len(user_dbs)
        s["db_size_gb"] = round(sum((d["data_mb"] or 0) + (d["log_mb"] or 0) for d in user_dbs) / 1024, 1)
        s["databases"] = user_dbs
        s["risk_counts"] = dict(Counter(r["severity"] for r in risks))
        grouped: dict[tuple[str, str], list[dict]] = {}
        for r in risks:
            grouped.setdefault((r["severity"], r["title"]), []).append(r)
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        top = sorted(grouped.items(), key=lambda kv: (order[kv[0][0]], -len(kv[1])))[:10]
        s["top_risks"] = [{"severity": k[0], "title": k[1], "count": len(v), "example": f"{v[0]['database']}: {v[0]['object']}".strip(": "),
                           "detail": v[0]["detail"][:160], "doc": v[0]["doc"]} for k, v in top]
        s["azure_blockers"] = sum(1 for r in risks if r.get("azure_sql_database_only"))
        s["warnings"] = inv.get("warnings", [])
    if ssrs:
        reps = ssrs["reports"]
        used = [r for r in reps if (r["runs_365"] or 0) > 0]
        s["ssrs"] = {
            "total": ssrs["report_count"], "counts": ssrs["classification_counts"], "used": len(used),
            "unused": ssrs["report_count"] - len(used), "usage": ssrs["usage"], "subscriptions_known": ssrs["subscriptions_known"],
            "top_used": sorted(used, key=lambda r: -(r["runs_365"] or 0))[:20],
            "tables": ssrs["table_usage"][:15],
            "hard": {
                "custom_code": sum(1 for r in reps if r["custom_code"]), "subreports": sum(1 for r in reps if r["subreports"]),
                "drillthrough": sum(1 for r in reps if r["drillthrough"]), "subscriptions": sum(1 for r in reps if r["subscriptions"]),
                "custom_assemblies": sum(1 for r in reps if r["custom_assemblies"]), "parse_problems": sum(1 for r in reps if r["parse_error"] or r["ref_confidence"] == "low"),
            },
            "review": [r for r in reps if r["classification"] == "review"],
            "retire": [r for r in reps if r["classification"] == "retire"],
            "paginated_hint": sum(1 for r in reps if r["layout_hint"] == "paginated"),
            "interactive_hint": sum(1 for r in reps if r["layout_hint"] == "interactive"),
        }
    if val:
        s["validation"] = {"counts": dict(Counter(v["status"] for v in val)), "rows": val}
    s["next_steps"] = _next_steps(s)
    return s


def _next_steps(s: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    eos = s.get("eos")
    if eos and eos.get("days") is not None:
        if eos["days"] < 0:
            steps.append(f"{eos['name']} is out of support. Pick the upgrade target and a date; Extended Security Updates are a stop-gap, not a plan.")
        else:
            steps.append(f"{eos['name']} loses support on {eos['date']}. Pick the upgrade target and schedule it before then.")
    if s.get("risk_counts", {}).get("high"):
        steps.append("Fix or plan around the high-severity risks first. They are features that are removed or not available in the target.")
    if s.get("azure_blockers"):
        steps.append("If the target is Azure SQL Database, review the risks marked 'Azure SQL Database only'. Managed Instance or SQL Server 2022 avoids most of them.")
    ss = s.get("ssrs")
    if ss:
        if ss["counts"].get("retire"):
            steps.append(f"Confirm the {ss['counts']['retire']} retire candidate(s) with their owners and drop them from the move.")
        if ss["counts"].get("review"):
            steps.append(f"Walk through the {ss['counts']['review']} review report(s). Each has a stated reason.")
        if ss["counts"].get("move"):
            steps.append(f"Start the move with the {ss['counts']['move']} plain report(s), then run `validate` to compare results.")
        if (ss["usage"].get("log_window_days") or 0) < 365:
            steps.append("Extend the SSRS execution log retention (ExecutionLogDaysKept) so the next run can prove which reports are unused.")
    if s.get("have_ssrs") and not s.get("have_validation"):
        steps.append("After the first reports are moved, run `validate` against the old and new servers.")
    return steps


def write_report(out: Path, today: date | None = None) -> list[str]:
    s = build_summary(out, today)
    tpl = resources.files("db_agent.engines.sqlserver").joinpath("templates/summary.html.j2").read_text(encoding="utf-8")
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    (out / "summary.html").write_text(env.from_string(tpl).render(s=s), encoding="utf-8")
    rows: list[dict[str, Any]] = [{"item": "Generated", "value": s["generated"]}]
    if s.get("server"):
        rows += [{"item": "SQL Server", "value": f"{s['server']['product_version']} {s['server']['edition']}"},
                 {"item": "End of support", "value": s["eos"]["text"]},
                 {"item": "User databases", "value": f"{s['db_count']} ({s['db_size_gb']} GB)"},
                 {"item": "Risks", "value": ", ".join(f"{k}: {v}" for k, v in s["risk_counts"].items()) or "none"}]
    if s.get("ssrs"):
        c = s["ssrs"]["counts"]
        rows += [{"item": "Reports", "value": s["ssrs"]["total"]}, {"item": "Move", "value": c.get("move", 0)},
                 {"item": "Review", "value": c.get("review", 0)}, {"item": "Retire", "value": c.get("retire", 0)},
                 {"item": "Used in last 365 days", "value": s["ssrs"]["used"]},
                 {"item": "Usage log reaches back (days)", "value": s["ssrs"]["usage"].get("log_window_days")}]
    if s.get("validation"):
        rows += [{"item": f"Validation {k}", "value": v} for k, v in s["validation"]["counts"].items()]
    rows += [{"item": f"Next step {i}", "value": t} for i, t in enumerate(s["next_steps"], 1)]
    rows += [{"item": "", "value": ""}, {"item": "", "value": FOOTER}]
    update_sheet(out, "Summary", rows, ["item", "value"])
    order_workbook(out)
    return ["summary.html", "db-agent-sqlserver.xlsx (Summary)"]
