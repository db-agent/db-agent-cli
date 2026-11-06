"""Server and database inventory. Metadata only: no business table is ever read.

All SQL here is a fixed string and passes through the QueryGuard like everything else.
Row counts come from sys.dm_db_partition_stats.
"""

from __future__ import annotations

import re

from collections import defaultdict
from typing import Any, Callable

from .db import ConnectionConfig, DatabaseError, SafeConnection
from .guard import GuardError, QueryGuard

SYSTEM_DATABASES = {"master", "model", "msdb", "tempdb"}

Q_SERVER = """
SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
       CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
       CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
       CAST(SERVERPROPERTY('Collation') AS nvarchar(128)) AS collation,
       CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition,
       CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS server_name
"""

Q_DATABASES = """
SELECT d.name, d.database_id, d.compatibility_level, d.collation_name, d.state_desc,
       d.recovery_model_desc, d.is_published, d.is_subscribed, d.is_merge_published, d.is_distributor,
       CAST(SUM(CASE WHEN mf.type = 0 THEN CAST(mf.size AS bigint) ELSE 0 END) * 8 / 1024 AS bigint) AS data_mb,
       CAST(SUM(CASE WHEN mf.type = 1 THEN CAST(mf.size AS bigint) ELSE 0 END) * 8 / 1024 AS bigint) AS log_mb
FROM sys.databases d
LEFT JOIN sys.master_files mf ON mf.database_id = d.database_id
GROUP BY d.name, d.database_id, d.compatibility_level, d.collation_name, d.state_desc,
         d.recovery_model_desc, d.is_published, d.is_subscribed, d.is_merge_published, d.is_distributor
ORDER BY d.name
"""

# Azure SQL Database has no sys.master_files. Fall back to sys.databases alone (sizes unknown), then
# fill in the size of the connected database from sys.database_files.
Q_DATABASES_BASIC = """
SELECT d.name, d.database_id, d.compatibility_level, d.collation_name, d.state_desc,
       d.recovery_model_desc, d.is_published, d.is_subscribed, d.is_merge_published, d.is_distributor,
       CAST(NULL AS bigint) AS data_mb, CAST(NULL AS bigint) AS log_mb
FROM sys.databases d
ORDER BY d.name
"""
Q_DATABASES_BASIC_MIN = """
SELECT d.name, d.database_id, d.compatibility_level, d.collation_name, d.state_desc,
       CAST(NULL AS nvarchar(60)) AS recovery_model_desc,
       CAST(0 AS bit) AS is_published, CAST(0 AS bit) AS is_subscribed, CAST(0 AS bit) AS is_merge_published,
       CAST(0 AS bit) AS is_distributor, CAST(NULL AS bigint) AS data_mb, CAST(NULL AS bigint) AS log_mb
FROM sys.databases d
ORDER BY d.name
"""
Q_OWN_FILES = """
SELECT CAST(SUM(CASE WHEN type = 0 THEN CAST(size AS bigint) ELSE 0 END) * 8 / 1024 AS bigint) AS data_mb,
       CAST(SUM(CASE WHEN type = 1 THEN CAST(size AS bigint) ELSE 0 END) * 8 / 1024 AS bigint) AS log_mb
FROM sys.database_files
"""

Q_LINKED = "SELECT name, product, provider, data_source FROM sys.servers WHERE is_linked = 1"
Q_DEPRECATED_COUNTERS = (
    "SELECT RTRIM(instance_name) AS feature, cntr_value AS uses_since_restart "
    "FROM sys.dm_os_performance_counters "
    "WHERE object_name LIKE '%Deprecated Features%' AND cntr_value > 0 ORDER BY cntr_value DESC"
)
Q_MIRRORING = "SELECT DB_NAME(database_id) AS database_name FROM sys.database_mirroring WHERE mirroring_guid IS NOT NULL"
Q_SOAP = "SELECT name FROM sys.endpoints WHERE type_desc = 'SOAP'"
Q_AGENT_JOBS = """
SELECT j.name AS job_name, j.enabled, js.step_id, js.step_name, js.subsystem, js.database_name,
       js.command
FROM msdb.dbo.sysjobs j
JOIN msdb.dbo.sysjobsteps js ON js.job_id = j.job_id
ORDER BY j.name, js.step_id
"""
Q_AGENT_SCHEDULES = """
SELECT j.name AS job_name, s.name AS schedule_name, s.enabled, s.freq_type, s.freq_interval
FROM msdb.dbo.sysjobs j
JOIN msdb.dbo.sysjobschedules js ON js.job_id = j.job_id
JOIN msdb.dbo.sysschedules s ON s.schedule_id = js.schedule_id
"""

# Per-database queries
Q_TABLES = """
SELECT s.name AS schema_name, t.name AS table_name, t.object_id, t.create_date, t.modify_date
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE t.is_ms_shipped = 0
"""
Q_ROWCOUNTS = (
    "SELECT object_id, SUM(row_count) AS row_count FROM sys.dm_db_partition_stats "
    "WHERE index_id IN (0, 1) GROUP BY object_id"
)
Q_COLUMNS = """
SELECT o.object_id, s.name AS schema_name, o.name AS object_name, o.type AS object_type,
       c.column_id, c.name AS column_name, ty.name AS type_name, c.max_length, c.precision, c.scale,
       c.is_nullable, c.is_identity, c.is_computed
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
ORDER BY o.object_id, c.column_id
"""
Q_INDEXES = """
SELECT i.object_id, i.name AS index_name, i.type_desc, i.is_primary_key, i.is_unique,
       ic.key_ordinal, c.name AS column_name
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
JOIN sys.objects o ON o.object_id = i.object_id
WHERE o.is_ms_shipped = 0 AND o.type = 'U' AND i.type > 0 AND ic.is_included_column = 0
ORDER BY i.object_id, i.index_id, ic.key_ordinal
"""
Q_MODULES = """
SELECT s.name AS schema_name, o.name AS object_name, o.type AS object_type, o.type_desc,
       o.create_date, o.modify_date, m.uses_ansi_nulls, m.uses_quoted_identifier, m.definition
FROM sys.sql_modules m
JOIN sys.objects o ON o.object_id = m.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0
ORDER BY s.name, o.name
"""
Q_DB_TRIGGERS = """
SELECT t.name AS object_name, m.uses_ansi_nulls, m.uses_quoted_identifier, m.definition
FROM sys.triggers t JOIN sys.sql_modules m ON m.object_id = t.object_id
WHERE t.parent_class = 0 AND t.is_ms_shipped = 0
"""
Q_TRIGGERS = """
SELECT t.name AS trigger_name, t.parent_class_desc, OBJECT_NAME(t.parent_id) AS parent_name,
       t.is_disabled, t.is_instead_of_trigger
FROM sys.triggers t WHERE t.is_ms_shipped = 0
"""
Q_SYNONYMS = "SELECT name, base_object_name FROM sys.synonyms"
Q_XDEPS = """
SELECT OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema,
       OBJECT_NAME(d.referencing_id) AS referencing_object,
       d.referenced_server_name, d.referenced_database_name, d.referenced_schema_name, d.referenced_entity_name
FROM sys.sql_expression_dependencies d
WHERE d.referenced_server_name IS NOT NULL OR d.referenced_database_name IS NOT NULL
"""
Q_ASSEMBLIES = "SELECT name, permission_set_desc FROM sys.assemblies WHERE is_user_defined = 1"
Q_BROKER = "SELECT name FROM sys.service_queues WHERE is_ms_shipped = 0"
Q_FULLTEXT = "SELECT name FROM sys.fulltext_catalogs"
Q_STRETCH = """
SELECT s.name AS schema_name, t.name AS table_name
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE t.is_remote_data_archive_enabled = 1
"""
Q_FILESTREAM = "SELECT COUNT(*) AS n FROM sys.columns WHERE is_filestream = 1"
Q_STANDALONE_DEFAULTS_RULES = (
    "SELECT SCHEMA_NAME(schema_id) AS schema_name, name, type_desc FROM sys.objects "
    "WHERE type IN ('D', 'R') AND parent_object_id = 0 AND is_ms_shipped = 0"
)


def major_version(product_version: str) -> int:
    try:
        return int(str(product_version).split(".")[0])
    except (ValueError, IndexError):
        return 0


def _optional(
    warnings: list[str], label: str, fn: Callable[[], Any], default: Any
) -> Any:
    """Run a query that may be refused by permissions or missing in this version."""
    try:
        return fn()
    except (DatabaseError, GuardError) as exc:
        msg = str(exc)
        m = re.search(r"Invalid object name '([^']+)'", msg)
        if m:
            warnings.append(f"{label}: skipped, {m.group(1)} does not exist on this server type (expected on Azure SQL Database)")
        else:
            warnings.append(f"{label}: skipped ({msg[:160]})")
        return default


def collect_database(conn: SafeConnection, name: str, warnings: list[str]) -> dict[str, Any]:
    """Collect objects for the database that `conn` is connected to."""
    db: dict[str, Any] = {"name": name}
    tables = _optional(warnings, f"{name}: tables", lambda: conn.query(Q_TABLES), [])
    counts = {
        r["object_id"]: int(r["row_count"] or 0)
        for r in _optional(warnings, f"{name}: row counts", lambda: conn.query(Q_ROWCOUNTS), [])
    }
    cols_by_obj: dict[int, list[dict]] = defaultdict(list)
    objects: dict[int, dict] = {}
    for r in _optional(warnings, f"{name}: columns", lambda: conn.query(Q_COLUMNS), []):
        cols_by_obj[r["object_id"]].append(
            {k: r[k] for k in ("column_name", "type_name", "max_length", "precision", "scale", "is_nullable", "is_identity", "is_computed")}
        )
        objects[r["object_id"]] = {"schema": r["schema_name"], "name": r["object_name"], "type": (r["object_type"] or "").strip()}
    idx: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in _optional(warnings, f"{name}: indexes", lambda: conn.query(Q_INDEXES), []):
        e = idx[r["object_id"]].setdefault(
            r["index_name"] or "(heap)",
            {"type": r["type_desc"], "primary_key": bool(r["is_primary_key"]), "unique": bool(r["is_unique"]), "columns": []},
        )
        e["columns"].append(r["column_name"])
    db["tables"] = [
        {
            "schema": t["schema_name"],
            "name": t["table_name"],
            "rows": counts.get(t["object_id"]),
            "columns": cols_by_obj.get(t["object_id"], []),
            "indexes": [{"name": k, **v} for k, v in idx.get(t["object_id"], {}).items()],
            "created": str(t["create_date"]),
            "modified": str(t["modify_date"]),
        }
        for t in tables
    ]
    db["views"] = [
        {"schema": o["schema"], "name": o["name"], "columns": cols_by_obj.get(oid, [])}
        for oid, o in objects.items()
        if o["type"] == "V"
    ]
    modules = _optional(warnings, f"{name}: modules", lambda: conn.query(Q_MODULES), [])
    modules += [
        {**r, "schema_name": "(database trigger)", "object_type": "TR", "type_desc": "DATABASE_DDL_TRIGGER"}
        for r in _optional(warnings, f"{name}: database triggers", lambda: conn.query(Q_DB_TRIGGERS), [])
    ]
    db["modules"] = [
        {
            "schema": m["schema_name"],
            "name": m["object_name"],
            "type": m["type_desc"],
            "created": str(m.get("create_date", "")),
            "modified": str(m.get("modify_date", "")),
            "uses_ansi_nulls": m["uses_ansi_nulls"],
            "uses_quoted_identifier": m["uses_quoted_identifier"],
            "encrypted": m["definition"] is None,
            "definition": m["definition"],
        }
        for m in modules
    ]
    db["triggers"] = _optional(warnings, f"{name}: triggers", lambda: conn.query(Q_TRIGGERS), [])
    db["synonyms"] = _optional(warnings, f"{name}: synonyms", lambda: conn.query(Q_SYNONYMS), [])
    db["cross_references"] = _optional(warnings, f"{name}: cross-database references", lambda: conn.query(Q_XDEPS), [])
    db["assemblies"] = _optional(warnings, f"{name}: assemblies", lambda: conn.query(Q_ASSEMBLIES), [])
    db["service_broker_queues"] = _optional(warnings, f"{name}: service broker", lambda: conn.query(Q_BROKER), [])
    db["fulltext_catalogs"] = _optional(warnings, f"{name}: full-text", lambda: conn.query(Q_FULLTEXT), [])
    db["stretch_tables"] = _optional(warnings, f"{name}: stretch database", lambda: conn.query(Q_STRETCH), [])
    fs = _optional(warnings, f"{name}: filestream", lambda: conn.query(Q_FILESTREAM), [{"n": 0}])
    db["filestream_columns"] = int(fs[0]["n"]) if fs else 0
    db["standalone_defaults_rules"] = _optional(warnings, f"{name}: defaults and rules", lambda: conn.query(Q_STANDALONE_DEFAULTS_RULES), [])
    return db


def collect(
    cfg: ConnectionConfig,
    databases: list[str] | None = None,
    include_system: bool = False,
    guard: QueryGuard | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Collect the full inventory. `databases` limits which ones are read."""
    say = progress or (lambda _m: None)
    warnings: list[str] = []
    guard = guard or QueryGuard()
    inv: dict[str, Any] = {"warnings": warnings}
    with SafeConnection(cfg.with_database("master") if not cfg.database else cfg, guard) as master:
        inv["server"] = master.query(Q_SERVER)[0]
        try:
            all_dbs = master.query(Q_DATABASES)
        except DatabaseError as exc:
            if "master_files" not in str(exc):
                raise
            warnings.append("database sizes: sys.master_files is not available here (Azure SQL Database), sizes are for the connected database only")
            try:
                all_dbs = master.query(Q_DATABASES_BASIC)
            except DatabaseError:
                all_dbs = master.query(Q_DATABASES_BASIC_MIN)
            own = _optional(warnings, "own database size", lambda: master.query(Q_OWN_FILES), [])
            cur = _optional(warnings, "current database name", lambda: master.query("SELECT DB_NAME() AS n"), [])
            if own and cur:
                for d in all_dbs:
                    if d["name"] == cur[0]["n"]:
                        d["data_mb"], d["log_mb"] = own[0]["data_mb"], own[0]["log_mb"]
        inv["linked_servers"] = _optional(warnings, "linked servers", lambda: master.query(Q_LINKED), [])
        azure_db = inv["server"].get("engine_edition") == 5  # Azure SQL Database
        if azure_db:
            # Platform tooling and sample-data loading use deprecated features on the service's own behalf.
            inv["deprecated_feature_counters"] = []
            warnings.append("deprecated feature counters: not read on Azure SQL Database (the counts reflect the platform's own use)")
        else:
            inv["deprecated_feature_counters"] = _optional(
                warnings, "deprecated feature counters (needs VIEW SERVER STATE)", lambda: master.query(Q_DEPRECATED_COUNTERS), []
            )
        inv["mirroring"] = _optional(warnings, "database mirroring", lambda: master.query(Q_MIRRORING), [])
        inv["soap_endpoints"] = _optional(warnings, "soap endpoints", lambda: master.query(Q_SOAP), [])
    inv["databases"] = all_dbs
    wanted = [
        d["name"]
        for d in all_dbs
        if d["state_desc"] == "ONLINE"
        and (databases is None or d["name"] in databases)
        and (include_system or d["name"] not in SYSTEM_DATABASES)
    ]
    inv["objects"] = {}
    for name in wanted:
        say(f"reading {name}")
        try:
            with SafeConnection(cfg.with_database(name), guard) as c:
                inv["objects"][name] = collect_database(c, name, warnings)
        except DatabaseError as exc:
            warnings.append(f"{name}: skipped, cannot connect ({str(exc)[:160]})")
    if inv["server"].get("engine_edition") == 5:
        warnings.append("SQL Server Agent jobs: skipped, Azure SQL Database has no SQL Server Agent")
        inv["agent_jobs"], inv["agent_schedules"] = [], []
        return inv
    say("reading SQL Server Agent jobs")
    try:
        with SafeConnection(cfg.with_database("msdb"), guard) as c:
            jobs = c.query(Q_AGENT_JOBS)
            sched = c.query(Q_AGENT_SCHEDULES)
        inv["agent_jobs"] = jobs  # command text is scanned for risks, then dropped before saving
        inv["agent_schedules"] = sched
    except (DatabaseError, GuardError) as exc:
        warnings.append(f"SQL Server Agent jobs: skipped ({str(exc)[:160]})")
        inv["agent_jobs"], inv["agent_schedules"] = [], []
    return inv
