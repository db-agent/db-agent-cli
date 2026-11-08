"""The only module that talks to a SQL Server driver.

Nothing else in the package may import pymssql or pyodbc (a test enforces this). Every
statement goes through SafeConnection.query() or .stream(), which call the QueryGuard first.
Passwords are held in memory only, hidden from repr(), and scrubbed from error messages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterator

from .guard import QueryGuard


@dataclass
class ConnectionConfig:
    server: str
    port: int = 1433
    database: str = ""
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    driver: str = "pymssql"  # pymssql | odbc
    trusted: bool = False  # Windows auth, odbc only
    encrypt: bool = True
    trust_server_certificate: bool = False
    login_timeout: int = 15
    query_timeout: int = 60

    def with_database(self, database: str) -> "ConnectionConfig":
        copy = ConnectionConfig(**{**self.__dict__})
        copy.database = database
        return copy

    def __repr__(self) -> str:  # never show the password
        return (
            f"ConnectionConfig(server={self.server!r}, port={self.port}, database={self.database!r}, "
            f"user={self.user!r}, driver={self.driver!r}, trusted={self.trusted})"
        )


ODBC_PREFERENCE = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 13.1 for SQL Server",
                   "ODBC Driver 13 for SQL Server", "ODBC Driver 11 for SQL Server")


def odbc_driver_name(installed: list[str]) -> str:
    """Newest installed Microsoft ODBC driver for SQL Server. Client machines often have only 17 or 13.
    Override with DBAGENT_ODBC_DRIVER."""
    import os

    forced = os.environ.get("DBAGENT_ODBC_DRIVER")
    if forced:
        return forced
    for name in ODBC_PREFERENCE:
        if name in installed:
            return name
    raise DatabaseError("no Microsoft ODBC driver for SQL Server found; install ODBC Driver 18 (or 17)")


class DatabaseError(Exception):
    """A driver or connection error, with secrets removed from the message."""


def scrub(text: str, secrets: list[str | None]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class SafeConnection:
    def __init__(self, cfg: ConnectionConfig, guard: QueryGuard | None = None):
        self.cfg = cfg
        self.guard = guard or QueryGuard()
        self._conn: Any = None

    # -- connection ---------------------------------------------------------------------------
    def _connect(self) -> Any:
        cfg = self.cfg
        try:
            if cfg.driver == "pymssql":
                import pymssql

                kwargs: dict[str, Any] = dict(
                    server=cfg.server,
                    port=str(cfg.port),
                    user=cfg.user,
                    password=cfg.password,
                    database=cfg.database or "",
                    login_timeout=cfg.login_timeout,
                    timeout=cfg.query_timeout,
                    appname="db-agent",
                    read_only=True,  # ApplicationIntent=ReadOnly
                    autocommit=False,
                )
                kwargs["encryption"] = "require" if cfg.encrypt else "off"
                return pymssql.connect(**kwargs)
            if cfg.driver == "odbc":
                import pyodbc

                parts = [
                    f"DRIVER={{{odbc_driver_name(pyodbc.drivers())}}}",
                    f"SERVER={cfg.server},{cfg.port}",
                    "ApplicationIntent=ReadOnly",
                    "APP=db-agent",
                    f"Encrypt={'yes' if cfg.encrypt else 'no'}",
                    f"TrustServerCertificate={'yes' if cfg.trust_server_certificate else 'no'}",
                ]
                if cfg.database:
                    parts.append(f"DATABASE={cfg.database}")
                if cfg.trusted:
                    parts.append("Trusted_Connection=yes")
                else:
                    parts += [f"UID={cfg.user}", "PWD={" + str(cfg.password).replace("}", "}}") + "}"]
                return pyodbc.connect(";".join(parts), timeout=cfg.login_timeout, autocommit=False)
        except Exception as exc:  # noqa: BLE001
            msg = scrub(f"could not connect to {cfg.server}: {exc}", [cfg.password])
            if "certificate" in msg.lower() and "trust" in msg.lower():
                msg += " Hint: the server uses a certificate this machine does not trust. Add --trust-server-certificate (odbc driver) or --no-encrypt."
            raise DatabaseError(msg) from None
        raise DatabaseError(f"unknown driver {cfg.driver!r}")

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "SafeConnection":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- queries ------------------------------------------------------------------------------
    def _cursor(self, sql: str, params: Any) -> Any:
        self.guard.check(sql)  # raises GuardError before anything reaches the server
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params) if params is not None else cur.execute(sql)
        except Exception as exc:  # noqa: BLE001
            cur.close()
            self.conn.rollback()
            raise DatabaseError(scrub(str(exc), [self.cfg.password])) from None
        return cur

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        """Run a guarded SELECT and return all rows as dicts. For metadata-sized results only."""
        cur = self._cursor(sql, params)
        try:
            cols = [d[0] for d in cur.description or []]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()
            self.conn.rollback()

    def stream(self, sql: str, params: Any = None, chunk: int = 5000) -> Iterator[tuple[list[str], list[tuple]]]:
        """Run a guarded SELECT and yield (columns, rows) chunks. Used by validate so rows are
        folded into counts and totals as they arrive and are never kept."""
        cur = self._cursor(sql, params)
        try:
            cols = [d[0] for d in cur.description or []]
            while True:
                rows = cur.fetchmany(chunk)
                if not rows:
                    break
                yield cols, rows
        finally:
            cur.close()
            self.conn.rollback()


def config_from_env(prefix: str = "DBAGENT_SQL") -> dict[str, str | None]:
    return {k: os.environ.get(f"{prefix}_{k.upper()}") for k in ("server", "port", "database", "user", "password")}
