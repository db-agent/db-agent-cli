"""Turn an inventory into upgrade risks. Pure functions: no database access."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .inventory import major_version
from .rules import (
    COMPAT_BY_MAJOR,
    DOC_AZURE_DIFF,
    DOC_COMPAT,
    DOC_DEPRECATED,
    DOC_DISCONTINUED,
    scan_code,
)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
DEPRECATED_TYPES = {"text", "ntext", "image"}


@dataclass
class Risk:
    id: str
    severity: str
    category: str
    title: str
    database: str
    object: str
    detail: str
    doc: str
    azure_sql_database_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(schema: str | None, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def assess(inv: dict[str, Any], target_major: int = 16) -> list[Risk]:
    """target_major: 15 = SQL Server 2019, 16 = SQL Server 2022."""
    risks: list[Risk] = []
    known_dbs = {d["name"].lower() for d in inv["databases"]}
    server_major = major_version(inv["server"]["product_version"])
    target_compat = COMPAT_BY_MAJOR.get(target_major, 160)

    for d in inv["databases"]:
        lvl = d["compatibility_level"]
        if d["name"] in ("master", "model", "msdb", "tempdb"):
            continue
        if lvl <= 100:
            risks.append(Risk("compat-deprecated", "high", "compatibility", f"Compatibility level {lvl} is deprecated",
                              d["name"], "", f"Level {lvl} is deprecated and removed in a future version. Test the application at {target_compat}.", DOC_COMPAT))
        elif lvl < target_compat:
            sev = "medium" if lvl <= 120 else "low"
            risks.append(Risk("compat-below-target", sev, "compatibility", f"Compatibility level {lvl} is below {target_compat}",
                              d["name"], "", f"Levels 110 and 120 are marked for deprecation. Moving to {target_compat} changes query behaviour (cardinality estimator). Test before and after.", DOC_COMPAT))
        if d["name"].upper() == "DQS_MAIN":
            risks.append(Risk("dqs", "medium", "feature", "Data Quality Services database found", d["name"], "",
                              "DQS is deprecated since SQL Server 2016 and removed in SQL Server 2025.", DOC_DISCONTINUED))
        if d.get("is_published") or d.get("is_merge_published") or d.get("is_subscribed") or d.get("is_distributor"):
            risks.append(Risk("replication", "medium", "feature", "Replication is configured", d["name"], "",
                              "Replication needs a plan for the upgrade order (distributor, publisher, subscriber). Azure SQL Database can only be a subscriber.", DOC_AZURE_DIFF, True))

    for ls in inv.get("linked_servers", []):
        risks.append(Risk("linked-server", "medium", "dependency", "Linked server", "(server)", ls["name"],
                          f"Provider {ls.get('provider')}. Linked servers are not available in Azure SQL Database. Check the provider (SQLOLEDB is deprecated).",
                          DOC_AZURE_DIFF, True))
        if str(ls.get("provider", "")).upper() == "SQLOLEDB":
            risks.append(Risk("dep-sqloledb", "medium", "deprecated", "Linked server uses the SQLOLEDB provider", "(server)", ls["name"],
                              "Use Microsoft OLE DB Driver for SQL Server.", DOC_DEPRECATED))

    for m in inv.get("mirroring", []):
        risks.append(Risk("mirroring", "high", "deprecated", "Database mirroring in use", m["database_name"], "",
                          "Database mirroring is deprecated. Use Always On availability groups or log shipping.", DOC_DEPRECATED))
    for e in inv.get("soap_endpoints", []):
        risks.append(Risk("soap-endpoint", "high", "deprecated", "SOAP endpoint", "(server)", e["name"],
                          "Native XML web services (SOAP endpoints) are deprecated. Use WCF or ASP.NET.", DOC_DEPRECATED))
    for c in inv.get("deprecated_feature_counters", []):
        m = re.fullmatch(r"Database compatibility level (\d+)", c["feature"].strip(), re.IGNORECASE)
        if m and int(m.group(1)) > 120:
            continue  # only levels 100, 110 and 120 are on Microsoft's deprecated list
        risks.append(Risk("runtime-deprecated-feature", "medium", "deprecated", f"Deprecated feature used since last restart: {c['feature']}",
                          "(server)", c["feature"], f"Used {c['uses_since_restart']} time(s) since the instance last restarted, by the server's own count. "
                          "This can include management tools and the server itself, so check the code that uses it.",
                          DOC_DEPRECATED))

    for dbname, db in inv.get("objects", {}).items():
        for t in db["tables"]:
            for c in t["columns"]:
                if c["type_name"] in DEPRECATED_TYPES:
                    risks.append(Risk("dep-text-ntext-image", "medium", "deprecated", f"Deprecated data type {c['type_name']}", dbname,
                                      f"{_fmt(t['schema'], t['name'])}.{c['column_name']}", "Use varchar(max), nvarchar(max) or varbinary(max).", DOC_DEPRECATED))
        for s in db["stretch_tables"]:
            risks.append(Risk("stretch", "high", "discontinued", "Stretch Database is enabled", dbname, _fmt(s["schema_name"], s["table_name"]),
                              "Stretch Database was discontinued in July 2024 in all supported versions. Data in Azure must be brought back on premises.", DOC_DISCONTINUED))
        for a in db["assemblies"]:
            risks.append(Risk("clr", "medium", "feature", "CLR assembly", dbname, a["name"],
                              f"Permission set {a['permission_set_desc']}. CLR is not available in Azure SQL Database (Managed Instance supports it). Review the code before an upgrade.", DOC_AZURE_DIFF, True))
        for q in db["service_broker_queues"]:
            risks.append(Risk("service-broker", "medium", "feature", "Service Broker queue", dbname, q["name"],
                              "Service Broker is not available in Azure SQL Database.", DOC_AZURE_DIFF, True))
        for f in db["fulltext_catalogs"]:
            risks.append(Risk("fulltext", "low", "feature", "Full-text catalog", dbname, f["name"],
                              "Rebuild and test full-text indexes after an upgrade.", DOC_AZURE_DIFF))
        if db["filestream_columns"]:
            risks.append(Risk("filestream", "medium", "feature", "FILESTREAM columns", dbname, "",
                              f"{db['filestream_columns']} column(s). Not available in Azure SQL Database.", DOC_AZURE_DIFF, True))
        for x in db["standalone_defaults_rules"]:
            risks.append(Risk("dep-create-default-rule", "low", "deprecated", f"Stand-alone {x['type_desc']}", dbname, _fmt(x["schema_name"], x["name"]),
                              "Use DEFAULT and CHECK constraints.", DOC_DEPRECATED))
        for x in db["cross_references"]:
            target = ".".join(p for p in (x["referenced_server_name"], x["referenced_database_name"], x["referenced_schema_name"], x["referenced_entity_name"]) if p)
            if x["referenced_server_name"]:
                risks.append(Risk("cross-server-ref", "high", "dependency", "Reference through a linked server", dbname,
                                  _fmt(x["referencing_schema"], x["referencing_object"]), f"References {target}.", DOC_AZURE_DIFF, True))
            elif x["referenced_database_name"] and x["referenced_database_name"].lower() != dbname.lower():
                if x["referenced_database_name"].lower() in known_dbs:
                    risks.append(Risk("cross-db-ref", "medium", "dependency", "Cross-database reference", dbname,
                                      _fmt(x["referencing_schema"], x["referencing_object"]), f"References {target}. Cross-database queries are not supported in Azure SQL Database.", DOC_AZURE_DIFF, True))
                else:
                    # The dependency view also lists XML method calls (for example Resume.ref.value) as if they were
                    # database references, so a name that is not a database here cannot be called a dependency with
                    # certainty. It can also be a real database that is missing from, or lives outside, this server.
                    risks.append(Risk("cross-db-ref-unresolved", "low", "dependency", "Reference to a name that is not a database on this server",
                                      dbname, _fmt(x["referencing_schema"], x["referencing_object"]),
                                      f"References {target}. Either a database that lives elsewhere (check it is moved too) or an XML method call that the "
                                      "dependency view lists as a database reference.", DOC_AZURE_DIFF))
        for s in db["synonyms"]:
            parts = re.sub(r"[\[\]]", "", s["base_object_name"] or "").split(".")
            if len(parts) >= 4:
                risks.append(Risk("synonym-linked-server", "high", "dependency", "Synonym points through a linked server", dbname, s["name"], s["base_object_name"], DOC_AZURE_DIFF, True))
            elif len(parts) == 3 and parts[0].lower() != dbname.lower():
                risks.append(Risk("synonym-cross-db", "medium", "dependency", "Synonym points to another database", dbname, s["name"], s["base_object_name"], DOC_AZURE_DIFF, True))
        for m in db["modules"]:
            obj = _fmt(m["schema"], m["name"])
            if m["encrypted"]:
                risks.append(Risk("encrypted-module", "info", "coverage", "Module is encrypted, code could not be scanned", dbname, obj,
                                  f"{m['type']} created WITH ENCRYPTION. Ask the vendor or owner.", DOC_DEPRECATED))
                continue
            if m["uses_ansi_nulls"] == 0:
                risks.append(Risk("dep-set-ansi-off", "medium", "deprecated", "Module stored with ANSI_NULLS OFF", dbname, obj,
                                  "ANSI_NULLS OFF is deprecated and will always be ON in a future version.", DOC_DEPRECATED))
            seen: set[tuple[str, int]] = set()
            for rule, line, text in scan_code(m["definition"] or ""):
                if (rule.id, line) in seen:
                    continue
                seen.add((rule.id, line))
                risks.append(Risk(rule.id, rule.severity, "deprecated", rule.title, dbname, obj,
                                  f"Line {line}: {text[:80]}. {rule.replacement}", rule.doc))
            if re.search(r"\bxp_cmdshell\b", m["definition"] or "", re.IGNORECASE):
                risks.append(Risk("xp-cmdshell", "medium", "feature", "Uses xp_cmdshell", dbname, obj,
                                  "Not available in Azure SQL Database. Review the security implications.", DOC_AZURE_DIFF, True))

    for job in inv.get("agent_jobs", []):
        cmd = job.get("command") or ""
        label = f"{job['job_name']} step {job['step_id']}"
        if job.get("subsystem") == "TSQL":
            for rule, line, text in scan_code(cmd):
                risks.append(Risk(rule.id, rule.severity, "deprecated", rule.title, job.get("database_name") or "(agent)", label,
                                  f"Line {line}: {text[:80]}. {rule.replacement}", rule.doc))
    if inv.get("agent_jobs"):
        names = {j["job_name"] for j in inv["agent_jobs"]}
        risks.append(Risk("agent-jobs", "low", "feature", f"{len(names)} SQL Server Agent job(s)", "(msdb)", "",
                          "SQL Server Agent is not available in Azure SQL Database (use elastic jobs or another scheduler). Move the jobs with the databases.", DOC_AZURE_DIFF, True))

    if server_major in (13, 14):
        risks.append(Risk("end-of-support", "high", "lifecycle", f"SQL Server {inv['server']['product_version']} is at or near end of support",
                          "(server)", "", "See the lifecycle dates in the summary.", "https://learn.microsoft.com/en-us/lifecycle/products/sql-server-2016"
                          if server_major == 13 else "https://learn.microsoft.com/en-us/lifecycle/products/sql-server-2017"))
    risks.sort(key=lambda r: (SEVERITY_ORDER[r.severity], r.category, r.database, r.object))
    return risks


def strip_secrets(inv: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe to save: Agent step commands and linked-server data sources are dropped
    (they can hold credentials). Module code is kept in a separate file by the caller."""
    safe = {k: v for k, v in inv.items() if k not in ("agent_jobs",)}
    safe["agent_jobs"] = [{k: v for k, v in j.items() if k != "command"} for j in inv.get("agent_jobs", [])]
    safe["linked_servers"] = [{k: v for k, v in ls.items() if k != "data_source"} for ls in inv.get("linked_servers", [])]
    return safe
