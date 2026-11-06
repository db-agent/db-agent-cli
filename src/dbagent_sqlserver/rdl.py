"""Parse SSRS report definitions (RDL 2008, 2010, 2016) and shared datasets/data sources.

Namespace-agnostic: element names are compared without the namespace, so one code path handles
every schema version. Connection strings are reduced to server and database names; passwords
are never kept (only a flag that one was present).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

RDL_NAMESPACES = {
    "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition": "2016",
    "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition": "2010",
    "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition": "2008",
    "http://schemas.microsoft.com/sqlserver/reporting/2005/01/reportdefinition": "2005",
}
INTERACTIVE_ELEMENTS = {"Chart", "GaugePanel", "Map", "Indicator", "Sparkline", "DataBar", "CustomReportItem"}
LAYOUT_ELEMENTS = {"Tablix", "Textbox", "Image", "Rectangle", "Line", "Subreport"} | INTERACTIVE_ELEMENTS
NON_SQL_PROVIDERS = {"OLEDB-MD", "SSAS", "ESSBASE", "SAPBW", "XML", "SHAREPOINTLIST", "ODATA", "AZUREBLOB"}
LOOKUP_RE = re.compile(r"\b(Lookup|LookupSet|MultiLookup)\s*\(", re.IGNORECASE)
CODE_CALL_RE = re.compile(r"\bCode\.\w+", re.IGNORECASE)


@dataclass
class DataSource:
    name: str
    shared_reference: str | None = None
    provider: str | None = None
    server: str | None = None
    database: str | None = None
    integrated_security: bool | None = None
    embedded_credentials: bool = False


@dataclass
class DataSet:
    name: str
    data_source: str | None = None
    command_type: str = "Text"  # Text | StoredProcedure | TableDirect
    command_text: str = ""
    query_parameters: list[dict[str, str]] = field(default_factory=list)
    shared_dataset_reference: str | None = None
    field_count: int = 0


@dataclass
class Report:
    file: str
    name: str
    folder: str
    path: str  # /Folder/Name, the form SSRS uses
    rdl_version: str = "unknown"
    data_sources: list[DataSource] = field(default_factory=list)
    datasets: list[DataSet] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    subreports: list[str] = field(default_factory=list)
    drillthrough: list[str] = field(default_factory=list)
    has_custom_code: bool = False
    custom_code_lines: int = 0
    custom_assemblies: bool = False
    embedded_images: int = 0
    expressions: int = 0
    complex_expressions: int = 0
    layout: dict[str, int] = field(default_factory=dict)
    has_page_header_footer: bool = False
    toggles: int = 0
    parse_error: str | None = None


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _children(el: etree._Element, name: str) -> list[etree._Element]:
    return [c for c in el if _local(c) == name]


def _child(el: etree._Element, name: str) -> etree._Element | None:
    for c in el:
        if _local(c) == name:
            return c
    return None


def _text(el: etree._Element | None, name: str | None = None) -> str | None:
    node = _child(el, name) if (el is not None and name) else el
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _iter(el: etree._Element, name: str):
    for n in el.iter():
        if _local(n) == name:
            yield n


def _parse_connect_string(cs: str | None) -> tuple[str | None, str | None, bool | None, bool]:
    """Return (server, database, integrated_security, had_password). The string is not kept."""
    if not cs or cs.strip().startswith("="):
        return None, None, None, False  # an expression: cannot be resolved statically
    pairs = {k.strip().lower(): v.strip() for k, _, v in (p.partition("=") for p in cs.split(";")) if k.strip()}
    server = pairs.get("data source") or pairs.get("server") or pairs.get("address") or pairs.get("addr")
    database = pairs.get("initial catalog") or pairs.get("database")
    integ = pairs.get("integrated security") or pairs.get("trusted_connection")
    integrated = None if integ is None else integ.lower() in ("true", "yes", "sspi")
    had_pw = any(k in pairs for k in ("password", "pwd")) and bool(pairs.get("password") or pairs.get("pwd"))
    return server, database, integrated, had_pw


def _parse_datasources(root: etree._Element) -> list[DataSource]:
    out: list[DataSource] = []
    holder = _child(root, "DataSources")
    for ds in _children(holder, "DataSource") if holder is not None else []:
        item = DataSource(name=ds.get("Name", ""))
        item.shared_reference = _text(ds, "DataSourceReference")
        conn = _child(ds, "ConnectionProperties")
        if conn is not None:
            item.provider = _text(conn, "DataProvider")
            item.server, item.database, item.integrated_security, item.embedded_credentials = _parse_connect_string(
                _text(conn, "ConnectString")
            )
            if _text(conn, "IntegratedSecurity"):
                item.integrated_security = _text(conn, "IntegratedSecurity").lower() == "true"
        out.append(item)
    return out


def _parse_dataset(ds: etree._Element) -> DataSet:
    item = DataSet(name=ds.get("Name", ""))
    q = _child(ds, "Query")
    if q is not None:
        item.data_source = _text(q, "DataSourceName")
        item.command_type = _text(q, "CommandType") or "Text"
        item.command_text = _text(q, "CommandText") or ""
        qp = _child(q, "QueryParameters")
        for p in _children(qp, "QueryParameter") if qp is not None else []:
            item.query_parameters.append({"name": p.get("Name", ""), "value": _text(p, "Value") or ""})
    shared = _child(ds, "SharedDataSet")
    if shared is not None:
        item.shared_dataset_reference = _text(shared, "SharedDataSetReference")
    fields = _child(ds, "Fields")
    item.field_count = len(_children(fields, "Field")) if fields is not None else 0
    return item


def _count_expressions(root: etree._Element) -> tuple[int, int]:
    total = complex_ = 0
    for el in root.iter():
        if not isinstance(el.tag, str) or el.text is None:
            continue
        t = el.text.strip()
        if t.startswith("=") and _local(el) not in ("CommandText", "Code"):
            total += 1
            if CODE_CALL_RE.search(t) or LOOKUP_RE.search(t):
                complex_ += 1
    return total, complex_


def parse_rdl_bytes(data: bytes, file: str, base: Path | None = None) -> Report:
    p = Path(file)
    rel = p.relative_to(base) if base and p.is_absolute() and str(p).startswith(str(base)) else p
    folder = "/" + "/".join(rel.parent.parts) if rel.parent.parts else "/"
    name = rel.stem
    path = (folder.rstrip("/") + "/" + name) if folder != "/" else "/" + name
    rep = Report(file=str(rel), name=name, folder=folder, path=path)
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        rep.parse_error = f"invalid XML: {str(exc)[:120]}"
        return rep
    if _local(root) != "Report":
        rep.parse_error = f"not a report definition (root element {_local(root)})"
        return rep
    rep.rdl_version = RDL_NAMESPACES.get(etree.QName(root).namespace or "", "unknown")
    rep.data_sources = _parse_datasources(root)
    ds_holder = _child(root, "DataSets")
    rep.datasets = [_parse_dataset(d) for d in _children(ds_holder, "DataSet")] if ds_holder is not None else []
    params = _child(root, "ReportParameters")
    for rp in _children(params, "ReportParameter") if params is not None else []:
        default = None
        dv = _child(rp, "DefaultValue")
        if dv is not None:
            vals = _child(dv, "Values")
            first = _children(vals, "Value")[0] if vals is not None and _children(vals, "Value") else None
            default = first.text.strip() if first is not None and first.text else None
        rep.parameters.append(
            {
                "name": rp.get("Name", ""),
                "type": _text(rp, "DataType"),
                "prompt": _text(rp, "Prompt"),
                "multi_value": (_text(rp, "MultiValue") or "").lower() == "true",
                "hidden": (_text(rp, "Hidden") or "").lower() == "true",
                "default": default,
                "default_from_query": _child(dv, "DataSetReference") is not None if dv is not None else False,
            }
        )
    rep.subreports = [_text(s, "ReportName") or "" for s in _iter(root, "Subreport")]
    rep.drillthrough = [_text(d, "ReportName") or "" for d in _iter(root, "Drillthrough")]
    code = _child(root, "Code")
    if code is not None and (code.text or "").strip():
        rep.has_custom_code = True
        rep.custom_code_lines = len([ln for ln in code.text.splitlines() if ln.strip()])
    rep.custom_assemblies = _child(root, "CodeModules") is not None or _child(root, "Classes") is not None
    rep.embedded_images = len(list(_iter(_child(root, "EmbeddedImages"), "EmbeddedImage"))) if _child(root, "EmbeddedImages") is not None else 0
    rep.expressions, rep.complex_expressions = _count_expressions(root)
    counts: dict[str, int] = {}
    for el in root.iter():
        n = _local(el)
        if n in LAYOUT_ELEMENTS:
            counts[n] = counts.get(n, 0) + 1
    rep.layout = counts
    rep.has_page_header_footer = any(True for _ in _iter(root, "PageHeader")) or any(True for _ in _iter(root, "PageFooter"))
    rep.toggles = len(list(_iter(root, "ToggleItem")))
    return rep


def parse_rdl_file(path: Path, base: Path | None = None) -> Report:
    try:
        data = path.read_bytes()
    except OSError as exc:
        p = Path(path)
        rel = p.relative_to(base) if base else p
        return Report(file=str(rel), name=p.stem, folder="/", path="/" + p.stem, parse_error=f"cannot read file: {exc.strerror}")
    return parse_rdl_bytes(data, str(path), base)


def parse_shared_dataset_bytes(data: bytes) -> DataSet | None:
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return None
    ds = _child(root, "DataSet")
    return _parse_dataset(ds) if ds is not None else None


def parse_shared_datasource_bytes(data: bytes) -> DataSource | None:
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return None
    ds = DataSource(name="")
    ds.provider = _text(root, "Extension")
    cs = _text(root, "ConnectString")
    ds.server, ds.database, ds.integrated_security, ds.embedded_credentials = _parse_connect_string(cs)
    return ds


def layout_hint(rep: Report) -> str:
    """Hint only: 'paginated' if the report is tabular and printable, 'interactive' if it leans on
    charts, gauges, maps or show/hide toggles, 'unknown' when it cannot be told."""
    if rep.parse_error:
        return "unknown"
    if any(k in rep.layout for k in INTERACTIVE_ELEMENTS) or rep.toggles:
        return "interactive"
    if rep.layout.get("Tablix") or rep.has_page_header_footer:
        return "paginated"
    return "unknown"


def non_sql_sources(rep: Report) -> list[str]:
    return sorted({(d.provider or "").upper() for d in rep.data_sources if (d.provider or "").upper() in NON_SQL_PROVIDERS})
