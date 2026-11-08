"""Classify each report as move, review or retire, with the reasons."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .rdl import layout_hint, non_sql_sources
from .usage import ReportUsage, UsageData


def load_config(path: Path | None = None) -> dict[str, Any]:
    default = yaml.safe_load(resources.files("db_agent.engines.sqlserver").joinpath("data/classification.yaml").read_text())
    if path:
        user = yaml.safe_load(Path(path).read_text()) or {}
        review = {**default.get("review_if", {}), **user.get("review_if", {})}
        default.update(user)
        default["review_if"] = review
    return default


def classify(
    report: Any,
    usage: UsageData | None,
    cfg: dict[str, Any],
    subscriptions: dict[str, Any] | None = None,
    unresolved_shared: bool = False,
    sql_parse_failed: list[str] | None = None,
) -> dict[str, Any]:
    """Return {classification, reasons, usage_known, layout_hint, ...} for one parsed report."""
    reasons: list[str] = []
    rv = cfg["review_if"]
    u: ReportUsage | None = None
    usage_known = usage is not None and usage.source in ("csv", "executionlog3")
    if usage_known:
        u = usage.get(report.path)

    # 1. Retire, only when the log is long enough to prove it.
    if usage_known and u is not None and u.runs_365 == 0:
        if usage.window_days >= cfg["min_log_window_days"]:
            return _result(report, usage, u, "retire", [f"No runs in the last {cfg['retire_after_days']} days (log covers {usage.window_days} days)"])
        reasons.append(
            f"No runs in the log, but the log covers only {usage.window_days} days, so it cannot show the report is unused. "
            f"Raise ExecutionLogDaysKept or supply a longer log"
        )

    # 2. Review reasons.
    if report.parse_error and rv.get("parse_failure", True):
        reasons.append(f"Report definition could not be parsed ({report.parse_error})")
    if sql_parse_failed and rv.get("parse_failure", True):
        reasons.append(f"Dataset SQL could not be fully parsed ({', '.join(sql_parse_failed)}); table list is a guess")
    if report.has_custom_code and rv.get("custom_code", True):
        reasons.append(f"Custom code block ({report.custom_code_lines} lines)")
    if report.custom_assemblies and rv.get("custom_assemblies", True):
        reasons.append("Uses custom assemblies or classes")
    if report.subreports and rv.get("subreports", True):
        reasons.append(f"Subreports: {', '.join(report.subreports)}")
    if report.drillthrough and rv.get("drillthrough", True):
        reasons.append(f"Drillthrough to: {', '.join(sorted(set(report.drillthrough)))}")
    subs = (subscriptions or {}).get(report.path.lower())
    if subs and rv.get("complex_subscription", True) and (subs.get("data_driven") or subs.get("non_email")):
        kinds = [k for k, v in (("data-driven", subs.get("data_driven")), ("non-email delivery", subs.get("non_email"))) if v]
        reasons.append(f"Subscriptions: {subs['count']} ({', '.join(kinds)})")
    ns = non_sql_sources(report)
    if ns and rv.get("non_sql_data_source", True):
        reasons.append(f"Non-SQL data source: {', '.join(ns)}")
    if unresolved_shared and rv.get("unresolved_shared_dataset", True):
        reasons.append("Uses a shared dataset or data source that was not supplied")
    if report.expressions > cfg["complex_expression_threshold"]:
        reasons.append(f"{report.expressions} expressions (threshold {cfg['complex_expression_threshold']})")
    if rv.get("stored_procedure_dataset", False) and any(d.command_type == "StoredProcedure" for d in report.datasets):
        reasons.append("Dataset calls a stored procedure")
    if usage_known and u is not None and 0 < u.runs_365 < cfg["low_usage_runs_365"]:
        reasons.append(f"Low usage: {u.runs_365} runs in 365 days")
    if usage_known and u is not None and u.runs_365 and u.errors / max(u.runs_365, 1) > cfg["error_rate_review"]:
        reasons.append(f"{u.errors} of {u.runs_365} runs failed")

    label = "review" if reasons else "move"
    if label == "move":
        reasons = ["Plain report, no hard-to-move features" + ("" if usage_known else "; usage unknown")]
    return _result(report, usage, u, label, reasons, subs)


def _result(report: Any, usage: UsageData | None, u: ReportUsage | None, label: str, reasons: list[str], subs: dict | None = None) -> dict[str, Any]:
    usage_known = usage is not None
    return {
        "classification": label,
        "reasons": reasons,
        "usage_known": usage_known,
        "log_window_days": usage.window_days if usage else None,
        "layout_hint": layout_hint(report),
        "last_run": u.last_run.strftime("%Y-%m-%d") if (u and u.last_run) else None,
        "runs_90": u.runs_90 if u else None,
        "runs_365": u.runs_365 if u else None,
        "distinct_users": u.distinct_users if u else None,
        "avg_ms": u.avg_ms if u else None,
        "errors": u.errors if u else None,
        "subscriptions": (subs or {}).get("count", 0),
    }
