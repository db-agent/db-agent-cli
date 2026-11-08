"""Engine registry. One entry per database engine the CLI knows about.

To add an engine, create `db_agent/engines/<name>/` with a `cli.py` that exposes a Typer `app`, then add
an `Engine(...)` below with status "available". See docs/adding-an-engine.md.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import typer


@dataclass(frozen=True)
class Engine:
    name: str            # the CLI verb: db-agent <name> ...
    title: str
    summary: str
    status: str          # "available" or "planned"
    module: str | None = None  # dotted path to a module exposing a Typer `app` (available engines only)

    def load(self) -> typer.Typer:
        if self.status != "available" or not self.module:
            raise LookupError(f"engine {self.name!r} is {self.status}, not implemented")
        return importlib.import_module(self.module).app


ENGINES: tuple[Engine, ...] = (
    Engine("sqlserver", "SQL Server and SSRS", "Inventory, migration risk, SSRS report triage and validation.",
           "available", "db_agent.engines.sqlserver.cli"),
    Engine("postgres", "PostgreSQL", "Not implemented yet.", "planned"),
    Engine("mysql", "MySQL", "Not implemented yet.", "planned"),
    Engine("databricks", "Databricks", "Workspace health: runtimes, legacy dependencies, DBU spend, spend waste.",
           "available", "db_agent.engines.databricks.cli"),
)
