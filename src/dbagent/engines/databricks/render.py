from __future__ import annotations

import json
from pathlib import Path

from .model import Report

_GROUPS = [("runtime.", "Runtimes"), ("legacy.", "Legacy dependencies"), ("cost.", "Cost over time"), ("waste.", "Spend waste")]


def to_json(rep: Report) -> str:
    return json.dumps({"meta": rep.meta, "counts": rep.counts(), "cost": rep.cost,
                       "findings": [f.__dict__ for f in rep.findings], "not_collected": rep.not_collected},
                      indent=2, default=str)


def _table(items: list[dict], limit: int = 15) -> list[str]:
    if not items:
        return []
    cols = list(items[0].keys())
    out = ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in items[:limit]:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "/") for c in cols) + " |")
    if len(items) > limit:
        out.append(f"\n{len(items) - limit} more in the JSON file.")
    return out


def to_markdown(rep: Report) -> str:
    c = rep.counts()
    m = rep.meta
    lines = ["# Databricks workspace health", "",
             f"Workspace {m.get('workspace_id')} at {m.get('host')}. Generated {m.get('generated_utc')} by {m.get('tool')}. "
             f"Read-only. Spend figures are list price. Lookback {m.get('lookback_days')} days.", "",
             f"**{c['high']} high, {c['medium']} medium, {c['low']} low, {c['info']} info.**", ""]
    for prefix, title in _GROUPS:
        fs = [f for f in rep.findings if f.id.startswith(prefix)]
        if not fs:
            continue
        lines += [f"## {title}", ""]
        for f in fs:
            lines += [f"### [{f.severity.upper()}] {f.title}", "", f.detail]
            if f.fix:
                lines += ["", f"Fix: {f.fix}"]
            lines += _table(f.items)
            lines.append("")
    if rep.cost and rep.cost["months"]:
        lines += ["### Monthly figures", "", "| month | DBU | list $ | days | $/day | $/DBU | tier |", "|---|---|---|---|---|---|---|"]
        for x in rep.cost["months"]:
            lines.append(f"| {x['month']}{' (partial)' if x['partial'] else ''} | {x['dbus']:,.0f} | {x['cost']:,.0f} | {x['days']} | "
                         f"{x['cost_per_day']:,.0f} | {(('%.3f' % x['rate']) if x['rate'] else 'n/a')} | {x['dominant_tier']} |")
        lines.append("")
    if rep.not_collected:
        lines += ["## Not collected", "", "These checks did not run. Results above do not cover them.", ""]
        lines += [f"- {n['check']}: {n['reason']}" for n in rep.not_collected]
    return "\n".join(lines) + "\n"


def write(rep: Report, out: Path, formats: set[str]) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    if "json" in formats:
        paths.append(out / "databricks-health.json")
        paths[-1].write_text(to_json(rep))
    if "md" in formats:
        paths.append(out / "databricks-health.md")
        paths[-1].write_text(to_markdown(rep))
    return paths


def console(rep: Report) -> str:
    c = rep.counts()
    lines = [f"Databricks health: {c['high']} high, {c['medium']} medium, {c['low']} low, {c['info']} info", ""]
    for f in rep.findings:
        lines.append(f"  [{f.severity.upper():<6}] {f.title}")
    if rep.not_collected:
        lines += ["", f"Not collected ({len(rep.not_collected)}):"] + [f"  - {n['check']}: {n['reason'][:110]}" for n in rep.not_collected]
    return "\n".join(lines)
