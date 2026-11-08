"""db-agent databricks <verb>."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import cost as cost_mod, health as health_mod, render

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Read-only checks of a Databricks workspace.")


@app.callback()
def _root() -> None:
    """Databricks engine."""


@app.command()
def health(
    profile: Annotated[Optional[str], typer.Option("--profile", help="Profile in ~/.databrickscfg. Without it, DATABRICKS_HOST/DATABRICKS_TOKEN or the default profile is used. Tokens are never taken on the command line.")] = None,
    host: Annotated[Optional[str], typer.Option("--host", help="Workspace URL.")] = None,
    warehouse_id: Annotated[Optional[str], typer.Option("--warehouse-id", help="SQL warehouse for system table queries. Default: a warehouse that is already running. A stopped warehouse starts (and bills) when it is used.")] = None,
    no_sql: Annotated[bool, typer.Option("--no-sql", help="REST API checks only. Skips every system table query.")] = False,
    lookback_days: Annotated[int, typer.Option("--lookback-days", min=1, max=365, help="Window for waste checks.")] = 30,
    cost_from: Annotated[Optional[str], typer.Option("--cost-from", help="First month for the cost comparison, YYYY-MM. Default: two months before the current one.")] = None,
    cost_to: Annotated[Optional[str], typer.Option("--cost-to", help="Last month, YYYY-MM. Default: the current month.")] = None,
    eos_warning_days: Annotated[int, typer.Option("--eos-warning-days", help="Flag runtimes that end support within this many days.")] = 180,
    all_workspaces: Annotated[bool, typer.Option("--all-workspaces", help="Include every workspace in the account in billing queries.")] = False,
    out: Annotated[Path, typer.Option("--out", help="Output folder.")] = Path("dbagent-databricks-output"),
    format: Annotated[str, typer.Option("--format", help="Comma list of: md, json.")] = "md,json",
) -> None:
    """Workspace health: runtimes, legacy dependencies, DBU spend over time, spend waste."""
    formats = {f.strip().lower() for f in format.split(",") if f.strip()}
    if formats - {"md", "json"}:
        raise typer.BadParameter("--format accepts md and json")
    if bool(cost_from) != bool(cost_to):
        raise typer.BadParameter("give both --cost-from and --cost-to, or neither")
    if cost_from:
        try:
            cost_mod.month_list(cost_from, cost_to)
        except (ValueError, TypeError) as e:
            raise typer.BadParameter(f"--cost-from/--cost-to: {e}")
    from .client import DatabricksError, connect   # imports the SDK only when the command runs
    try:
        reader = connect(profile, host, warehouse_id, use_sql=not no_sql)
    except Exception as e:   # noqa: BLE001
        typer.echo(f"Could not connect to Databricks: {e}\nCheck `databricks auth login` or DATABRICKS_HOST/DATABRICKS_TOKEN.", err=True)
        raise typer.Exit(2)
    if not no_sql and not reader.has_sql:
        typer.echo("No running SQL warehouse found. Pass --warehouse-id (it will start if stopped) or --no-sql.", err=True)
    try:
        rep = health_mod.run(reader, health_mod.Options(date.today(), lookback_days, cost_from, cost_to, eos_warning_days, all_workspaces))
    except DatabricksError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    typer.echo(render.console(rep))
    for p in render.write(rep, out, formats):
        typer.echo(f"wrote {p}")
