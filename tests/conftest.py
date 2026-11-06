from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def examples() -> Path:
    return EXAMPLES


@pytest.fixture()
def fake_inventory() -> dict:
    """A small hand-made inventory shaped like inventory.collect() output."""
    def mod(schema, name, typ, code, ansi=1, enc=False):
        return {"schema": schema, "name": name, "type": typ, "created": "", "modified": "", "uses_ansi_nulls": ansi,
                "uses_quoted_identifier": 1, "encrypted": enc, "definition": None if enc else code}
    db = {
        "name": "Sales",
        "tables": [{"schema": "dbo", "name": "Orders", "rows": 1200, "columns": [
            {"column_name": "Notes", "type_name": "text", "max_length": 16, "precision": 0, "scale": 0, "is_nullable": 1, "is_identity": 0, "is_computed": 0},
            {"column_name": "Id", "type_name": "int", "max_length": 4, "precision": 10, "scale": 0, "is_nullable": 0, "is_identity": 1, "is_computed": 0}],
            "indexes": [], "created": "", "modified": ""}],
        "views": [],
        "modules": [
            mod("dbo", "usp_old", "SQL_STORED_PROCEDURE",
                "CREATE PROC dbo.usp_old AS\nSET ROWCOUNT 10;\nSELECT * FROM sysobjects WHERE x = 1;\nEXEC sp_addlogin 'a';\n"
                "SELECT HASHBYTES('MD5', 'x');\nSELECT * FROM t (NOLOCK);\nSELECT * FROM t WITH (NOLOCK);\n-- SET ROWCOUNT 5 in a comment\nSELECT 'sp_addrole in a string';"),
            mod("dbo", "usp_ansi", "SQL_STORED_PROCEDURE", "SELECT 1", ansi=0),
            mod("dbo", "usp_secret", "SQL_STORED_PROCEDURE", None, enc=True),
            mod("dbo", "usp_cmd", "SQL_STORED_PROCEDURE", "EXEC xp_cmdshell 'dir'"),
        ],
        "triggers": [], "synonyms": [{"name": "syn_remote", "base_object_name": "[LS1].[Other].[dbo].[T]"},
                                    {"name": "syn_db", "base_object_name": "[Other].[dbo].[T]"}],
        "cross_references": [
            {"referencing_schema": "dbo", "referencing_object": "usp_old", "referenced_server_name": None,
             "referenced_database_name": "Other", "referenced_schema_name": "dbo", "referenced_entity_name": "T"},
            {"referencing_schema": "dbo", "referencing_object": "vw_xml", "referenced_server_name": None,
             "referenced_database_name": "Resume", "referenced_schema_name": "ref", "referenced_entity_name": "value"},
            {"referencing_schema": "dbo", "referencing_object": "vw_x", "referenced_server_name": "LS1",
             "referenced_database_name": "Remote", "referenced_schema_name": "dbo", "referenced_entity_name": "T"}],
        "assemblies": [{"name": "MyClr", "permission_set_desc": "SAFE_ACCESS"}],
        "service_broker_queues": [{"name": "Q1"}], "fulltext_catalogs": [{"name": "ft"}],
        "stretch_tables": [{"schema_name": "dbo", "table_name": "Orders"}], "filestream_columns": 0,
        "standalone_defaults_rules": [],
    }
    return {
        "warnings": [],
        "server": {"product_version": "13.0.7000.1", "edition": "Standard Edition (64-bit)", "product_level": "SP3", "collation": "SQL_Latin1_General_CP1_CI_AS",
                   "engine_edition": 2, "server_name": "SQL2016"},
        "databases": [
            {"name": "master", "database_id": 1, "compatibility_level": 130, "collation_name": "x", "state_desc": "ONLINE", "recovery_model_desc": "SIMPLE",
             "is_published": 0, "is_subscribed": 0, "is_merge_published": 0, "is_distributor": 0, "data_mb": 10, "log_mb": 2},
            {"name": "Sales", "database_id": 5, "compatibility_level": 100, "collation_name": "x", "state_desc": "ONLINE", "recovery_model_desc": "FULL",
             "is_published": 1, "is_subscribed": 0, "is_merge_published": 0, "is_distributor": 0, "data_mb": 2048, "log_mb": 512},
            {"name": "Old", "database_id": 6, "compatibility_level": 110, "collation_name": "x", "state_desc": "ONLINE", "recovery_model_desc": "FULL",
             "is_published": 0, "is_subscribed": 0, "is_merge_published": 0, "is_distributor": 0, "data_mb": 100, "log_mb": 10},
            {"name": "Other", "database_id": 7, "compatibility_level": 160, "collation_name": "x", "state_desc": "ONLINE", "recovery_model_desc": "FULL",
             "is_published": 0, "is_subscribed": 0, "is_merge_published": 0, "is_distributor": 0, "data_mb": 1, "log_mb": 1}],
        "linked_servers": [{"name": "LS1", "product": "SQL Server", "provider": "SQLOLEDB", "data_source": "host;password=hunter2"}],
        "deprecated_feature_counters": [{"feature": "sp_addlogin", "uses_since_restart": 4},
                                        {"feature": "Database compatibility level 140", "uses_since_restart": 9},
                                        {"feature": "Database compatibility level 100", "uses_since_restart": 1}],
        "mirroring": [{"database_name": "Sales"}], "soap_endpoints": [],
        "objects": {"Sales": db},
        "agent_jobs": [{"job_name": "Nightly", "enabled": 1, "step_id": 1, "step_name": "s", "subsystem": "TSQL", "database_name": "Sales",
                        "command": "SET ROWCOUNT 5; -- password=hunter2\nEXEC sp_dboption 'x'"}],
        "agent_schedules": [],
    }
