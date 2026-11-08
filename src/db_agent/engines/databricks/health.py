"""Collect, run the checks, assemble the Report. Each collection step fails on its own: a missing permission
on one system table never hides the other results; it shows up under "not collected"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from db_agent import __version__

from . import checks, cost as cost_mod, queries, runtimes
from .model import Finding, Report


@dataclass
class Options:
    today: date
    lookback_days: int = 30
    cost_from: str | None = None
    cost_to: str | None = None
    eos_warning_days: int = 180
    all_workspaces: bool = False


def _num(v):
    return None if v is None else float(v)


def run(reader, opt: Options) -> Report:
    rep = Report(meta={"tool": f"db-agent {__version__}", "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "lookback_days": opt.lookback_days, "runtime_table_source": runtimes.SOURCE,
                       "prices": "list price (system.billing.list_prices), before discounts"})

    def step(name: str, fn: Callable, default=None):
        try:
            return fn()
        except Exception as e:   # noqa: BLE001 - every failure is reported, none is fatal
            rep.not_collected.append({"check": name, "reason": f"{type(e).__name__}: {str(e)[:300]}"})
            return default

    wid = step("workspace id", reader.workspace_id)
    rep.meta["workspace_id"] = wid
    rep.meta["host"] = step("host", reader.host)
    ws_filter = None if opt.all_workspaces else wid
    if not opt.all_workspaces and wid is None:
        rep.not_collected.append({"check": "system table queries", "reason": "workspace id unknown; pass --all-workspaces to include every workspace in the account"})
    sql_ok = reader.has_sql and (opt.all_workspaces or wid is not None)
    rep.meta["warehouse_id"] = reader.warehouse_id if sql_ok else None
    if not reader.has_sql:
        why = "skipped by --no-sql" if getattr(reader, "sql_skipped", False) else "no SQL warehouse is running; pass --warehouse-id (a stopped one will start and bill)"
        rep.not_collected.append({"check": "system table queries", "reason": why})

    clusters = step("clusters (REST)", reader.clusters, [])
    jobs = step("jobs (REST)", reader.jobs, [])
    warehouses = step("SQL warehouses (REST)", reader.warehouses, [])
    rep.meta["counts"] = {"clusters": len(clusters), "jobs": len(jobs), "warehouses": len(warehouses)}

    # 1. runtimes
    rep.findings += checks.runtime_findings(clusters, jobs, opt.today, opt.eos_warning_days)

    # SQL-backed data
    def q(name, sql):
        return step(name, lambda: reader.sql(sql), None) if sql_ok else None

    cluster_spend = q("spend by cluster", queries.spend_by_cluster(opt.lookback_days, ws_filter))
    cluster_spend = {r["cluster_id"]: r for r in cluster_spend} if cluster_spend is not None else None
    wh_spend = q("spend by warehouse", queries.spend_by_warehouse(opt.lookback_days, ws_filter))
    wh_spend = {r["warehouse_id"]: r for r in wh_spend} if wh_spend is not None else None
    wh_queries = q("queries by warehouse (system.query.history)", queries.queries_by_warehouse(opt.lookback_days, ws_filter))
    wh_queries = {r["warehouse_id"]: r for r in wh_queries} if wh_queries is not None else None
    untagged = q("untagged spend", queries.untagged_spend(opt.lookback_days, ws_filter))
    mounts = q("DBFS mount events (system.access.audit)", queries.dbfs_mount_events(365, ws_filter))

    hms = None
    if sql_ok:
        def hms_counts():
            try:
                schemas = [list(r.values())[0] for r in reader.sql(queries.SHOW_SCHEMAS)]
            except Exception as e:   # noqa: BLE001
                if "HIVE_METASTORE_DISABLED" in str(e):
                    rep.findings.append(Finding("legacy.hive_metastore_disabled", "info", "Hive metastore is disabled in this workspace",
                                                "Legacy access to hive_metastore is turned off, so there are no Hive metastore tables to migrate here.", [], ""))
                    return {}
                raise
            if len(schemas) > 300:
                rep.not_collected.append({"check": "Hive metastore tables", "reason": f"{len(schemas)} schemas; counted the first 300"})
                schemas = schemas[:300]
            out = {}
            for s in schemas:
                if s == "information_schema":
                    continue
                rows = reader.sql(queries.show_tables(s))
                out[s] = sum(1 for r in rows if str(r.get("isTemporary", "false")).lower() != "true")
            return out
        hms = step("Hive metastore tables (SHOW TABLES)", hms_counts, None)

    # 2. legacy dependencies
    rep.findings += checks.hms_findings(hms)
    rep.findings += checks.mount_findings(mounts, 365)
    rep.findings += checks.init_script_findings(clusters, jobs)
    rep.findings += checks.isolation_findings(clusters, jobs)

    # 3. cost over time
    if sql_ok:
        a, b = (opt.cost_from, opt.cost_to) if opt.cost_from and opt.cost_to else cost_mod.default_range(opt.today)
        def cost_step():
            months = cost_mod.month_list(a, b)
            last = months[-1]
            end = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
            return cost_mod.analyze(reader.sql(queries.cost_by_month(months[0], end, ws_filter)), opt.today)
        res = step("DBU spend by month", cost_step, None)
        if res is not None:
            rep.cost = res
            rep.findings.append(_cost_finding(res, a, b))

    # 4. waste
    rep.findings += checks.jobs_on_all_purpose(jobs, clusters, cluster_spend, opt.lookback_days)
    rep.findings += checks.no_autotermination(clusters, cluster_spend, opt.lookback_days)
    rep.findings += checks.warehouse_findings(warehouses, wh_spend, wh_queries, opt.lookback_days)
    rep.findings += checks.untagged_findings(untagged, opt.lookback_days)

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rep.findings.sort(key=lambda f: order[f.severity])
    return rep


def _usd(x: float, signed: bool = False) -> str:
    s = f"{abs(x):,.2f}" if abs(x) < 100 else f"{abs(x):,.0f}"
    sign = ("+" if x >= 0 else "-") if signed else ("-" if x < 0 else "")
    return f"{sign}${s}"


def _cost_finding(res: dict, a: str, b: str) -> Finding:
    ms = res["months"]
    if not ms:
        return Finding("cost.mom", "info", f"No DBU usage found for {a} to {b}",
                       "System tables have no rows for these months in this workspace. They may not have been enabled yet.", [], "")
    lines = []
    for m in ms:
        rate = f"{_usd(m['rate'])}/DBU" if m["rate"] else "n/a"
        lines.append(f"{m['month']}: {m['dbus']:,.0f} DBU, {_usd(m['cost'])} ({m['days']} days of data{', partial month' if m['partial'] else ''}), "
                     f"{_usd(m['cost_per_day'])}/day, {rate}, tier {m['dominant_tier']}")
    last = ms[-1].get("vs_first")
    sev, extra = "info", ""
    if last and last["pct"] is not None:
        extra = (f" Against {ms[0]['month']}, cost per day moved {last['pct']:+.0f}% ({_usd(last['cost_per_day_change'], True)}/day): "
                 f"{_usd(last['volume_effect_per_day'], True)}/day from usage (volume and SKU mix), "
                 f"{_usd(last['price_effect_per_day'], True)}/day from list price per DBU.")
        if abs(last["pct"]) >= 20:
            sev = "medium"
    if ms[0]["days"] < 15 and len(ms) > 1:
        extra += f" Weak baseline: {ms[0]['month']} has only {ms[0]['days']} days of data, so the percentage change overstates the trend."
    if res["tier_changed"]:
        extra += " The SKU tier changed during this period: " + " then ".join(f"{m}={t}" for m, t in res["tier_sequence"]) + "."
    return Finding("cost.mom", sev, f"DBU spend {a} to {b}", "; ".join(lines) + "." + extra, [], "")
