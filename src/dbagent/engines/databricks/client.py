"""The only module that imports the Databricks SDK. Read-only by construction:

* REST: list/get calls only (clusters, jobs, warehouses, workspace id).
* SQL: statements go through sqlguard.check() and are SELECT/WITH or SHOW ... IN hive_metastore only.
tests/databricks/test_architecture.py enforces both rules.
"""

from __future__ import annotations

import time
from typing import Any

from . import sqlguard


class DatabricksError(Exception):
    pass


class Reader:
    def __init__(self, ws: Any, warehouse_id: str | None, sql_timeout_s: int = 300, sql_skipped: bool = False):
        self._ws = ws
        self.sql_skipped = sql_skipped
        self.warehouse_id = warehouse_id
        self._timeout = sql_timeout_s

    @property
    def has_sql(self) -> bool:
        return bool(self.warehouse_id)

    # ---- REST -------------------------------------------------------------------------------------
    def workspace_id(self) -> str:
        return str(self._ws.get_workspace_id())

    def host(self) -> str:
        return str(self._ws.config.host)

    def clusters(self) -> list[dict]:
        return [c.as_dict() for c in self._ws.clusters.list()]

    def jobs(self) -> list[dict]:
        return [j.as_dict() for j in self._ws.jobs.list(expand_tasks=True)]

    def warehouses(self) -> list[dict]:
        return [w.as_dict() for w in self._ws.warehouses.list()]

    # ---- SQL --------------------------------------------------------------------------------------
    def sql(self, statement: str) -> list[dict]:
        if not self.warehouse_id:
            raise DatabricksError("no SQL warehouse selected")
        sqlguard.check(statement)
        r = self._ws.statement_execution.execute_statement(
            statement=statement, warehouse_id=self.warehouse_id, wait_timeout="30s", row_limit=10000)
        deadline = time.monotonic() + self._timeout
        while r.status and r.status.state.value in ("PENDING", "RUNNING"):
            if time.monotonic() > deadline:
                self._ws.statement_execution.cancel_execution(r.statement_id)
                raise DatabricksError(f"query still running after {self._timeout}s, cancelled")
            time.sleep(2)
            r = self._ws.statement_execution.get_statement(r.statement_id)
        state = r.status.state.value if r.status and r.status.state else "UNKNOWN"
        if state != "SUCCEEDED":
            err = r.status.error.message if r.status and r.status.error else state
            raise DatabricksError(str(err)[:400])
        cols = [c.name for c in (r.manifest.schema.columns if r.manifest and r.manifest.schema else [])]
        rows = (r.result.data_array if r.result and r.result.data_array else [])
        return [dict(zip(cols, row)) for row in rows]


def connect(profile: str | None, host: str | None, warehouse_id: str | None, use_sql: bool = True) -> Reader:
    """Unified auth: --profile, DATABRICKS_HOST/DATABRICKS_TOKEN, OAuth from `databricks auth login`.
    A token is never accepted on the command line."""
    from databricks.sdk import WorkspaceClient   # lazy: keeps `db-agent --help` fast

    kw: dict[str, Any] = {}
    if profile:
        kw["profile"] = profile
    if host:
        kw["host"] = host
    ws = WorkspaceClient(**kw)
    chosen = warehouse_id
    if use_sql and not chosen:
        running = [w for w in ws.warehouses.list() if w.state and w.state.value == "RUNNING"]
        if running:
            chosen = running[0].id
    return Reader(ws, chosen if use_sql else None, sql_skipped=not use_sql)
