"""A fake workspace. SQL is routed by the marker comment each query starts with."""
from db_agent.engines.databricks.client import DatabricksError

CLUSTERS = [
    {"cluster_id": "c-old", "cluster_name": "legacy-etl", "spark_version": "13.3.x-scala2.12", "cluster_source": "UI",
     "autotermination_minutes": 0, "state": "RUNNING", "creator_user_name": "a@x.com", "data_security_mode": "NONE",
     "init_scripts": [{"dbfs": {"destination": "dbfs:/init/setup.sh"}}]},
    {"cluster_id": "c-ok", "cluster_name": "analytics", "spark_version": "15.4.x-scala2.12", "cluster_source": "UI",
     "autotermination_minutes": 60, "state": "TERMINATED", "data_security_mode": "USER_ISOLATION"},
    {"cluster_id": "c-job", "cluster_name": "job-1", "spark_version": "13.3.x-scala2.12", "cluster_source": "JOB"},
]
JOBS = [
    {"job_id": 1, "settings": {"name": "nightly", "schedule": {"quartz_cron_expression": "0 0 1 * * ?", "pause_status": "UNPAUSED"},
                               "tasks": [{"task_key": "t", "existing_cluster_id": "c-old"}]}},
    {"job_id": 2, "settings": {"name": "paused", "schedule": {"pause_status": "PAUSED"},
                               "tasks": [{"task_key": "t", "existing_cluster_id": "c-old"}]}},
    {"job_id": 3, "settings": {"name": "modern", "schedule": {"pause_status": "UNPAUSED"},
                               "job_clusters": [{"job_cluster_key": "k", "new_cluster": {"spark_version": "14.3.x-scala2.12", "data_security_mode": "SINGLE_USER"}}],
                               "tasks": [{"task_key": "t", "job_cluster_key": "k"}]}},
]
WAREHOUSES = [
    {"id": "w-idle", "name": "bi-wh", "state": "RUNNING", "cluster_size": "Small", "auto_stop_mins": 0},
    {"id": "w-busy", "name": "etl-wh", "state": "STOPPED", "cluster_size": "Medium", "auto_stop_mins": 10},
]
SQL = {
    "cost_by_month": [
        {"month": "2026-09-01", "sku_name": "STANDARD_ALL_PURPOSE_COMPUTE", "dbus": "100", "cost": "55", "days": "30"},
        {"month": "2026-10-01", "sku_name": "PREMIUM_ALL_PURPOSE_COMPUTE", "dbus": "100", "cost": "65", "days": "31"},
        {"month": "2026-11-01", "sku_name": "PREMIUM_ALL_PURPOSE_COMPUTE", "dbus": "30", "cost": "19.5", "days": "6"},
    ],
    "spend_by_cluster": [{"cluster_id": "c-old", "dbus": "900", "cost": "630.5"}],
    "spend_by_warehouse": [{"warehouse_id": "w-idle", "dbus": "50", "cost": "27.5"}, {"warehouse_id": "w-busy", "dbus": "80", "cost": "44"}],
    "queries_by_warehouse": [{"warehouse_id": "w-busy", "queries": "412", "last_query": "2026-11-06T10:00:00Z"}],
    "untagged_spend": [{"product": "ALL_PURPOSE", "cost": "700", "untagged_cost": "500", "dbus": "1", "untagged_dbus": "1"},
                       {"product": "JOBS", "cost": "300", "untagged_cost": "0", "dbus": "1", "untagged_dbus": "0"}],
    "dbfs_mount_events": [{"event_date": "2026-03-01", "user_email": "a@x.com", "params": "{\"mountPoint\":\"/mnt/raw\"}"}],
    "hms_schemas": [{"databaseName": "default"}, {"databaseName": "sales"}],
    "hms_tables": None,   # handled in FakeReader
}


class FakeReader:
    def __init__(self, sql=True, fail=(), clusters=CLUSTERS, jobs=JOBS, warehouses=WAREHOUSES, hms_disabled=False):
        self._hms_disabled = hms_disabled
        self.warehouse_id = "wh-1" if sql else None
        self._fail, self._c, self._j, self._w = set(fail), clusters, jobs, warehouses
        self.statements: list[str] = []

    has_sql = property(lambda self: bool(self.warehouse_id))
    def workspace_id(self): return "1234567890"
    def host(self): return "https://example.cloud.databricks.com"
    def clusters(self): return self._c
    def jobs(self): return self._j
    def warehouses(self): return self._w

    def sql(self, statement):
        self.statements.append(statement)
        name = statement.split("dbagent:", 1)[1].split(" ", 1)[0]
        if name in self._fail:
            raise DatabricksError("PERMISSION_DENIED: no SELECT on system.x")
        if name == "hms_schemas" and self._hms_disabled:
            raise DatabricksError("[UC_HIVE_METASTORE_DISABLED_EXCEPTION] The operation attempted to use Hive Metastore, which is disabled")
        if name == "hms_tables":
            return [{"database": "sales", "tableName": f"t{i}", "isTemporary": "false"} for i in range(3)] if "`sales`" in statement else \
                   [{"database": "default", "tableName": "tmp", "isTemporary": "true"}]
        return SQL[name]
