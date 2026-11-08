"""Pure checks: plain dicts in, Findings out. No network, no SDK."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

from . import runtimes
from .model import Finding

ALL_PURPOSE_SOURCES = {"UI", "API", None, ""}
NO_ISOLATION = {"NONE", "LEGACY_TABLE_ACL", "LEGACY_PASSTHROUGH", "LEGACY_SINGLE_USER", "LEGACY_SINGLE_USER_STANDARD"}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_all_purpose(c: dict) -> bool:
    return c.get("cluster_source") in ALL_PURPOSE_SOURCES


def _job_specs(jobs: Iterable[dict]) -> Iterable[dict]:
    """Every cluster definition inside job settings: {'job_id','job','where','spec'}."""
    for j in jobs:
        s = j.get("settings") or {}
        base = {"job_id": j.get("job_id"), "job": s.get("name")}
        for jc in s.get("job_clusters") or []:
            yield {**base, "where": f"job cluster {jc.get('job_cluster_key')}", "spec": jc.get("new_cluster") or {}}
        for t in s.get("tasks") or []:
            if t.get("new_cluster"):
                yield {**base, "where": f"task {t.get('task_key')}", "spec": t["new_cluster"]}


def _all_specs(clusters: list[dict], jobs: list[dict]) -> Iterable[dict]:
    for c in clusters:
        if is_all_purpose(c):
            yield {"job_id": None, "job": None, "where": f"cluster {c.get('cluster_name')} ({c.get('cluster_id')})",
                   "spec": c, "owner": c.get("creator_user_name")}
    yield from _job_specs(jobs)


# ---- runtimes -------------------------------------------------------------------------------------
def runtime_findings(clusters, jobs, today: date, warn_days: int) -> list[Finding]:
    rows, versions = [], Counter()
    for s in _all_specs(clusters, jobs):
        st = runtimes.classify(s["spec"].get("spark_version"), today, warn_days)
        versions[st.label] += 1
        if st.state in ("ended", "ending_soon"):
            rows.append({"where": ("job " + str(s["job"]) + ": " if s["job"] else "") + s["where"],
                         "runtime": st.label, "state": st.state,
                         "end_of_support": st.eos.isoformat() if st.eos else "ended (before 10.5)",
                         "days_left": st.days_left})
    out = []
    ended = [r for r in rows if r["state"] == "ended"]
    soon = [r for r in rows if r["state"] == "ending_soon"]
    if ended:
        out.append(Finding("runtime.ended", "high", f"{len(ended)} cluster definitions on runtimes past end of support",
                           "Databricks no longer supports these runtimes. Existing jobs may keep running, but they get no fixes.",
                           ended, "Move to a supported LTS runtime (check the support table in the Databricks docs)."))
    if soon:
        out.append(Finding("runtime.ending_soon", "medium", f"{len(soon)} cluster definitions on runtimes that end support within {warn_days} days",
                           "Plan the upgrade before the end-of-support date.", soon, "Test the workload on the next LTS runtime."))
    unknown = sum(n for lbl, n in versions.items() if lbl.startswith("custom") or lbl == "unknown")
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(versions.items())) or "none found"
    if unknown:
        detail += f". {unknown} could not be classified (custom image or version newer than this tool's table)."
    out.append(Finding("runtime.summary", "info", "Runtime versions in use", detail, [], ""))
    return out


# ---- legacy dependencies ----------------------------------------------------------------------------
def init_script_findings(clusters, jobs) -> list[Finding]:
    rows = []
    for s in _all_specs(clusters, jobs):
        for sc in s["spec"].get("init_scripts") or []:
            dest = (sc.get("dbfs") or {}).get("destination")
            if dest:
                rows.append({"where": ("job " + str(s["job"]) + ": " if s["job"] else "") + s["where"], "script": dest})
    if not rows:
        return []
    return [Finding("legacy.dbfs_init_scripts", "high", f"{len(rows)} init script references on DBFS",
                    "DBFS root is local to one workspace, so these scripts do not exist in a new workspace.",
                    rows, "Move the scripts to workspace files or a Unity Catalog volume and update the cluster definitions.")]


def isolation_findings(clusters, jobs) -> list[Finding]:
    rows, unset = [], 0
    for s in _all_specs(clusters, jobs):
        mode = s["spec"].get("data_security_mode")
        if mode in NO_ISOLATION:
            rows.append({"where": ("job " + str(s["job"]) + ": " if s["job"] else "") + s["where"], "data_security_mode": mode})
        elif mode is None:
            unset += 1
    out = []
    if rows:
        out.append(Finding("legacy.no_isolation", "medium", f"{len(rows)} cluster definitions without Unity Catalog access mode",
                           "No-isolation and legacy access modes cannot use Unity Catalog governance.", rows,
                           "Switch to standard (shared) or dedicated (single user) access mode."))
    if unset:
        out.append(Finding("legacy.access_mode_unset", "info", f"{unset} cluster definitions do not set an access mode",
                           "The effective mode then comes from workspace or policy defaults. Check them in the UI.", [], ""))
    return out


def hms_findings(schemas: dict[str, int] | None) -> list[Finding]:
    if not schemas:
        return []
    total = sum(schemas.values())
    if total == 0:
        return []
    rows = [{"schema": k, "tables": v} for k, v in sorted(schemas.items(), key=lambda kv: -kv[1]) if v][:25]
    return [Finding("legacy.hive_metastore", "medium", f"{total} tables in the Hive metastore across {len(rows)} schemas",
                    "Hive metastore tables belong to one workspace and are not governed by Unity Catalog. A new workspace will not see them.",
                    rows, "Upgrade them to Unity Catalog (SYNC for external tables, DEEP CLONE or CTAS for managed ones).")]


def mount_findings(events: list[dict] | None, lookback_days: int) -> list[Finding]:
    if not events:
        return []
    return [Finding("legacy.dbfs_mounts", "medium", f"{len(events)} DBFS mount events in the last {lookback_days} days",
                    "Audit logs show mount creation only. They do not list the mounts that exist today; run dbutils.fs.mounts() "
                    "in a notebook for that. Mounts are workspace-local and deprecated in favour of Unity Catalog external locations.",
                    [{"date": str(e.get("event_date")), "user": e.get("user_email"), "params": e.get("params")} for e in events[:25]],
                    "Replace /mnt paths with external locations or volumes.")]


# ---- waste -------------------------------------------------------------------------------------------
def _scheduled(settings: dict) -> bool:
    for key in ("schedule", "trigger", "continuous"):
        v = settings.get(key)
        if v and v.get("pause_status") != "PAUSED":
            return True
    return False


def jobs_on_all_purpose(jobs, clusters, spend: dict[str, dict] | None, days: int) -> list[Finding]:
    names = {c.get("cluster_id"): c for c in clusters}
    rows = []
    for j in jobs:
        s = j.get("settings") or {}
        if not _scheduled(s):
            continue
        ids = {t.get("existing_cluster_id") for t in s.get("tasks") or [] if t.get("existing_cluster_id")}
        if s.get("existing_cluster_id"):
            ids.add(s["existing_cluster_id"])
        for cid in sorted(ids):
            sp = (spend or {}).get(cid)
            rows.append({"job": s.get("name"), "job_id": j.get("job_id"), "cluster": (names.get(cid) or {}).get("cluster_name", cid),
                         "cluster_id": cid, f"cluster_spend_{days}d": round(_num(sp["cost"]), 2) if sp and sp.get("cost") is not None else None})
    if not rows:
        return []
    rows.sort(key=lambda r: -(r[f"cluster_spend_{days}d"] or 0))
    return [Finding("waste.jobs_on_all_purpose", "high", f"{len(rows)} scheduled jobs run on all-purpose clusters",
                    "All-purpose compute costs more per DBU than job compute. Spend shown is the whole cluster's list-price spend, "
                    "which includes anything else that ran on it (billing does not tag all-purpose usage with the job).",
                    rows, "Give each job its own job cluster, or use serverless jobs.")]


def no_autotermination(clusters, spend: dict[str, dict] | None, days: int) -> list[Finding]:
    rows = []
    for c in clusters:
        if not is_all_purpose(c) or c.get("autotermination_minutes") != 0:   # only an explicit 0 means disabled
            continue
        sp = (spend or {}).get(c.get("cluster_id"))
        rows.append({"cluster": c.get("cluster_name"), "cluster_id": c.get("cluster_id"), "state": c.get("state"),
                     "owner": c.get("creator_user_name"), f"spend_{days}d": round(_num(sp["cost"]), 2) if sp and sp.get("cost") is not None else None})
    if not rows:
        return []
    running = sum(1 for r in rows if r["state"] == "RUNNING")
    rows.sort(key=lambda r: (r["state"] != "RUNNING", -(r[f"spend_{days}d"] or 0)))
    return [Finding("waste.no_autotermination", "high" if running else "medium",
                    f"{len(rows)} all-purpose clusters never auto-terminate ({running} running now)",
                    "A cluster with auto-termination off bills until someone stops it.", rows,
                    "Set auto-termination (for example 30 to 60 minutes) or enforce it with a cluster policy.")]


def warehouse_findings(warehouses, spend: dict[str, dict] | None, queries: dict[str, dict] | None, days: int) -> list[Finding]:
    billed_idle, never_stop = [], []
    for w in warehouses:
        wid = w.get("id")
        sp = (spend or {}).get(wid)
        q = (queries or {}).get(wid)
        cost = _num(sp["cost"]) if sp and sp.get("cost") is not None else None
        nq = int(q["queries"]) if q else (0 if queries is not None else None)
        row = {"warehouse": w.get("name"), "id": wid, "state": w.get("state"), "size": w.get("cluster_size"),
               "auto_stop_mins": w.get("auto_stop_mins"), f"spend_{days}d": round(cost, 2) if cost is not None else None,
               f"queries_{days}d": nq, "last_query": q.get("last_query") if q else None}
        if nq == 0 and sp and _num(sp.get("dbus")) > 0:
            billed_idle.append(row)
        if w.get("auto_stop_mins") == 0:
            never_stop.append(row)
    out = []
    if billed_idle:
        out.append(Finding("waste.idle_warehouses", "high", f"{len(billed_idle)} SQL warehouses billed DBUs but ran no queries in {days} days",
                           "These warehouses were started and kept running without serving any query.", billed_idle,
                           "Stop or delete them, and lower auto-stop."))
    if never_stop:
        out.append(Finding("waste.warehouse_no_autostop", "medium", f"{len(never_stop)} SQL warehouses with auto-stop disabled",
                           "They run until someone stops them.", never_stop, "Set auto-stop to 10 minutes or less."))
    return out


def untagged_findings(rows: list[dict] | None, days: int) -> list[Finding]:
    if not rows:
        return []
    total = sum(_num(r.get("cost")) for r in rows)
    untagged = sum(_num(r.get("untagged_cost")) for r in rows)
    if total <= 0:
        return []
    share = untagged / total * 100
    items = [{"product": r["product"], "untagged_cost": round(_num(r.get("untagged_cost")), 2),
              "total_cost": round(_num(r.get("cost")), 2)} for r in rows if _num(r.get("untagged_cost")) > 0][:15]
    sev = "high" if share >= 50 else "medium" if share >= 20 else "low"
    return [Finding("waste.untagged_spend", sev, f"{share:.0f}% of spend has no tags (${untagged:,.0f} of ${total:,.0f} in {days} days)",
                    "Spend without tags cannot be charged back to a team or project. List price, before discounts.",
                    items, "Add custom tags on clusters, jobs and warehouses, and enforce them with a cluster policy.")]
