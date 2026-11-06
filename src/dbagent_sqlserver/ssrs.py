"""Build the SSRS report inventory from a folder of RDL files or from a ReportServer database."""

from __future__ import annotations

import copy
import csv
from collections import Counter
from pathlib import Path
from typing import Any

from . import classify as classify_mod
from . import rdl, sqlrefs
from .db import SafeConnection
from .usage import UsageData

# Catalog.Type values used by SSRS. Folder=1, Report=2, Resource=3, LinkedReport=4, DataSource=5,
# Model=6, Dataset=8. Only 2, 4, 5 and 8 are read here.
Q_CATALOG = (
    "SELECT ItemID, Path, Name, Type, LinkSourceID, Content FROM dbo.Catalog WHERE Type IN (2, 4, 5, 8)"
)
Q_SUBSCRIPTIONS = """
SELECT c.Path AS ItemPath, COUNT(*) AS subs,
       SUM(CASE WHEN s.DataSettings IS NOT NULL THEN 1 ELSE 0 END) AS data_driven,
       SUM(CASE WHEN s.DeliveryExtension <> 'Report Server Email' THEN 1 ELSE 0 END) AS non_email
FROM dbo.Subscriptions s JOIN dbo.Catalog c ON c.ItemID = s.Report_OID
GROUP BY c.Path
"""


def load_subscriptions_csv(path: Path) -> dict[str, dict[str, Any]]:
    """Sidecar file for folder mode. Columns: ItemPath, DeliveryExtension, DataDriven (yes/no)."""
    out: dict[str, dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            lr = {k.lower(): (v or "") for k, v in r.items()}
            key = lr.get("itempath", "").strip().lower()
            if not key:
                continue
            e = out.setdefault(key, {"count": 0, "data_driven": 0, "non_email": 0})
            e["count"] += 1
            e["data_driven"] += 1 if lr.get("datadriven", "").strip().lower() in ("yes", "true", "1") else 0
            e["non_email"] += 1 if lr.get("deliveryextension", "Report Server Email").strip() not in ("", "Report Server Email") else 0
    return out


def _unresolved(report: rdl.Report, shared_ds: dict[str, Any], shared_src: dict[str, Any]) -> bool:
    for d in report.datasets:
        if d.shared_dataset_reference and _key(d.shared_dataset_reference) not in shared_ds:
            return True
    for s in report.data_sources:
        if s.shared_reference and _key(s.shared_reference) not in shared_src:
            return True
    return False


def _key(ref: str) -> str:
    return ref.strip().rstrip("/").split("/")[-1].lower()


def _dataset_rows(report: rdl.Report, shared_ds: dict[str, rdl.DataSet]) -> list[dict[str, Any]]:
    rows = []
    for d in report.datasets:
        eff = d
        if d.shared_dataset_reference and _key(d.shared_dataset_reference) in shared_ds:
            sd = shared_ds[_key(d.shared_dataset_reference)]
            eff = rdl.DataSet(name=d.name, data_source=sd.data_source, command_type=sd.command_type, command_text=sd.command_text,
                              query_parameters=sd.query_parameters or d.query_parameters, field_count=sd.field_count)
        src = next((s for s in report.data_sources if s.name == eff.data_source), None)
        non_sql = bool(src and (src.provider or "").upper() in rdl.NON_SQL_PROVIDERS)
        refs = sqlrefs.Refs(confidence="n/a", error="non-SQL data source") if non_sql else sqlrefs.extract(eff.command_text, eff.command_type)
        rows.append(
            {
                "name": d.name,
                "shared_reference": d.shared_dataset_reference,
                "data_source": eff.data_source,
                "command_type": eff.command_type,
                "command_text": eff.command_text,
                "query_parameters": eff.query_parameters,
                "tables": refs.tables,
                "procedures": refs.procedures,
                "temp_tables": refs.temp_tables,
                "confidence": refs.confidence,
                "parse_error": refs.error,
            }
        )
    return rows


def _report_row(
    report: rdl.Report,
    shared_ds: dict[str, rdl.DataSet],
    shared_src: dict[str, Any],
    usage: UsageData | None,
    subs: dict[str, Any],
    cfg: dict[str, Any],
    linked_from: str | None = None,
) -> dict[str, Any]:
    datasets = _dataset_rows(report, shared_ds)
    unresolved = _unresolved(report, shared_ds, shared_src)
    failed = [d["name"] for d in datasets if d["confidence"] == "low"]
    cls = classify_mod.classify(report, usage, cfg, subs, unresolved, failed)
    servers = sorted({f"{s.server or '?'}/{s.database or '?'}" for s in report.data_sources if s.server or s.database})
    tables = sorted({t for d in datasets for t in d["tables"]}, key=str.lower)
    procs = sorted({p for d in datasets for p in d["procedures"]}, key=str.lower)
    confidences = {d["confidence"] for d in datasets if d["confidence"] != "n/a"}
    order = ["low", "medium", "high"]
    overall = min(confidences, key=order.index) if confidences else "n/a"
    return {
        "name": report.name,
        "folder": report.folder,
        "path": report.path,
        "file": report.file,
        "rdl_version": report.rdl_version,
        "linked_report_of": linked_from,
        **cls,
        "datasets": datasets,
        "dataset_count": len(datasets),
        "data_sources": [
            {"name": s.name, "shared_reference": s.shared_reference, "provider": s.provider, "server": s.server,
             "database": s.database, "integrated_security": s.integrated_security, "embedded_credentials": s.embedded_credentials}
            for s in report.data_sources
        ],
        "data_source_servers": servers,
        "tables": tables,
        "procedures": procs,
        "ref_confidence": overall,
        "parameters": report.parameters,
        "parameter_count": len(report.parameters),
        "subreports": report.subreports,
        "drillthrough": sorted(set(report.drillthrough)),
        "custom_code": report.has_custom_code,
        "custom_assemblies": report.custom_assemblies,
        "embedded_images": report.embedded_images,
        "expressions": report.expressions,
        "complex_expressions": report.complex_expressions,
        "layout": report.layout,
        "embedded_credentials": any(s.embedded_credentials for s in report.data_sources),
        "parse_error": report.parse_error,
    }


def from_folder(
    folder: Path,
    usage: UsageData | None,
    cfg: dict[str, Any],
    subscriptions_csv: Path | None = None,
) -> dict[str, Any]:
    folder = folder.resolve()
    reports = [rdl.parse_rdl_file(p, folder) for p in sorted(folder.rglob("*.rdl"))]
    shared_ds: dict[str, rdl.DataSet] = {}
    for p in folder.rglob("*.rsd"):
        d = rdl.parse_shared_dataset_bytes(p.read_bytes())
        if d:
            shared_ds[p.stem.lower()] = d
    shared_src: dict[str, Any] = {}
    for p in folder.rglob("*.rds"):
        s = rdl.parse_shared_datasource_bytes(p.read_bytes())
        if s:
            shared_src[p.stem.lower()] = s
    sidecar = subscriptions_csv or (folder / "subscriptions.csv")
    subs = load_subscriptions_csv(sidecar) if sidecar.exists() else {}
    rows = [_report_row(r, shared_ds, shared_src, usage, subs, cfg) for r in reports]
    return _wrap(rows, usage, "rdl-folder", subs_known=bool(subs))


def from_reportserver(conn: SafeConnection, usage: UsageData | None, cfg: dict[str, Any]) -> dict[str, Any]:
    catalog = conn.query(Q_CATALOG)
    subs_rows = conn.query(Q_SUBSCRIPTIONS)
    subs = {
        (r["ItemPath"] or "").lower(): {"count": int(r["subs"]), "data_driven": int(r["data_driven"] or 0), "non_email": int(r["non_email"] or 0)}
        for r in subs_rows
    }
    by_id = {r["ItemID"]: r for r in catalog}
    shared_ds: dict[str, rdl.DataSet] = {}
    shared_src: dict[str, Any] = {}
    for r in catalog:
        content = bytes(r["Content"]) if r["Content"] is not None else b""
        if r["Type"] == 8:
            d = rdl.parse_shared_dataset_bytes(content)
            if d:
                shared_ds[_key(r["Path"])] = d
        elif r["Type"] == 5:
            s = rdl.parse_shared_datasource_bytes(content)
            if s:
                shared_src[_key(r["Path"])] = s
    rows: list[dict[str, Any]] = []
    parsed_by_id: dict[Any, rdl.Report] = {}
    for r in catalog:
        if r["Type"] != 2:
            continue
        content = bytes(r["Content"]) if r["Content"] is not None else b""
        path = r["Path"]
        rep = rdl.parse_rdl_bytes(content, path.lstrip("/") + ".rdl", None)
        folder = path.rsplit("/", 1)[0] or "/"
        rep.name, rep.folder, rep.path = r["Name"], folder, path
        parsed_by_id[r["ItemID"]] = rep
        rows.append(_report_row(rep, shared_ds, shared_src, usage, subs, cfg))
    for r in catalog:
        if r["Type"] != 4:
            continue
        src = parsed_by_id.get(r["LinkSourceID"])
        if src is None:
            continue
        path = r["Path"]
        linked = copy.deepcopy(src)  # the source definition under the linked report's name
        linked.name, linked.folder, linked.path = r["Name"], path.rsplit("/", 1)[0] or "/", path
        rows.append(_report_row(linked, shared_ds, shared_src, usage, subs, cfg, linked_from=by_id[r["LinkSourceID"]]["Path"]))
    return _wrap(rows, usage, "reportserver", subs_known=True)


def _wrap(rows: list[dict[str, Any]], usage: UsageData | None, source: str, subs_known: bool) -> dict[str, Any]:
    rows.sort(key=lambda r: (r["folder"].lower(), r["name"].lower()))
    counts = Counter(r["classification"] for r in rows)
    table_use = Counter(t for r in rows for t in r["tables"])
    return {
        "source": source,
        "report_count": len(rows),
        "usage": {
            "source": usage.source if usage else None,
            "log_window_days": usage.window_days if usage else None,
            "oldest": usage.oldest.strftime("%Y-%m-%d") if usage and usage.oldest else None,
            "newest": usage.newest.strftime("%Y-%m-%d") if usage and usage.newest else None,
            "as_of": usage.as_of.strftime("%Y-%m-%d") if usage else None,
            "note": None if usage else "No usage data supplied. Reports are never classified retire without it.",
        },
        "subscriptions_known": subs_known,
        "classification_counts": dict(counts),
        "table_usage": table_use.most_common(),
        "reports": rows,
    }
