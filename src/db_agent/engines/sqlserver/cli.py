"""Command line: db-agent sqlserver <inventory|ssrs|validate|report|version>."""

from __future__ import annotations

import functools
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
import yaml

from . import SUPPORTED_SQL_SERVER, __version__, classify, outputs, report as report_mod, risks as risks_mod, ssrs as ssrs_mod, usage as usage_mod, validate as validate_mod
from . import inventory as inventory_mod
from .db import ConnectionConfig, DatabaseError, SafeConnection
from .guard import GuardError, QueryGuard

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Read-only inventory of SQL Server and SSRS reports before an upgrade or a move to Power BI.")

# ---- shared options -----------------------------------------------------------------------------
Server = Annotated[Optional[str], typer.Option("--server", envvar="DBAGENT_SQL_SERVER", help="SQL Server host name or address.")]
Port = Annotated[int, typer.Option("--port", envvar="DBAGENT_SQL_PORT", help="TCP port.")]
Database = Annotated[Optional[str], typer.Option("--database", envvar="DBAGENT_SQL_DATABASE", help="Database to connect to.")]
User = Annotated[Optional[str], typer.Option("--user", envvar="DBAGENT_SQL_USER", help="SQL login. The password comes from DBAGENT_SQL_PASSWORD or a hidden prompt.")]
Driver = Annotated[str, typer.Option("--driver", help="pymssql (default) or odbc (Windows authentication, Entra ID).")]
Trusted = Annotated[bool, typer.Option("--trusted", help="Windows authentication. Needs --driver odbc.")]
Encrypt = Annotated[bool, typer.Option("--encrypt/--no-encrypt", help="Encrypt the connection.")]
TrustCert = Annotated[bool, typer.Option("--trust-server-certificate", help="Accept a self-signed server certificate (odbc driver).")]
Out = Annotated[Path, typer.Option("--out", help="Folder for all output files.")]
Format = Annotated[str, typer.Option("--format", help="Comma list of: md, json, xlsx, html.")]


def _formats(value: str) -> set[str]:
    chosen = {f.strip().lower() for f in value.split(",") if f.strip()}
    bad = chosen - set(outputs.FORMATS)
    if bad:
        raise typer.BadParameter(f"unknown format(s): {', '.join(sorted(bad))}. Choose from {', '.join(outputs.FORMATS)}.")
    return chosen


def _password(user: str | None, trusted: bool, env: str = "DBAGENT_SQL_PASSWORD", label: str = "SQL Server") -> str | None:
    if trusted or not user:
        return None
    pw = os.environ.get(env)
    if pw is None:
        pw = typer.prompt(f"Password for {user} on {label}", hide_input=True)
    return pw


def _config(server, port, database, user, driver, trusted, encrypt, trust_cert) -> ConnectionConfig:
    if not server:
        raise typer.BadParameter("--server is required (or set DBAGENT_SQL_SERVER).")
    if driver not in ("pymssql", "odbc"):
        raise typer.BadParameter("--driver must be pymssql or odbc.")
    if trusted and driver != "odbc":
        raise typer.BadParameter("--trusted (Windows authentication) needs --driver odbc.")
    if not trusted and not user:
        raise typer.BadParameter("--user is required unless --trusted is used.")
    return ConnectionConfig(server=server, port=port, database=database or "", user=user, password=_password(user, trusted),
                            driver=driver, trusted=trusted, encrypt=encrypt, trust_server_certificate=trust_cert)


def parse_conn_spec(spec: str, env_prefix: str, label: str) -> ConnectionConfig:
    """server=host,port=1433,database=db,user=u,driver=odbc,trusted=yes,encrypt=no,trust_cert=yes

    The password is never part of the spec: set <env_prefix>_PASSWORD or you will be asked."""
    kv: dict[str, str] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        k, sep, v = part.partition("=")
        if not sep:
            raise typer.BadParameter(f"{label}: expected key=value pairs, got {part!r}")
        kv[k.strip().lower()] = v.strip()
    if "password" in kv or "pwd" in kv:
        raise typer.BadParameter(f"{label}: do not put the password on the command line. Set {env_prefix}_PASSWORD or wait for the prompt.")
    unknown = set(kv) - {"server", "port", "database", "user", "driver", "trusted", "encrypt", "trust_cert"}
    if unknown:
        raise typer.BadParameter(f"{label}: unknown key(s): {', '.join(sorted(unknown))}")
    yes = lambda v: v.lower() in ("1", "true", "yes")  # noqa: E731
    trusted = yes(kv.get("trusted", "no"))
    user = kv.get("user")
    return ConnectionConfig(
        server=kv.get("server", ""), port=int(kv.get("port", 1433)), database=kv.get("database", ""), user=user,
        password=_password(user, trusted, f"{env_prefix}_PASSWORD", label), driver=kv.get("driver", "pymssql"), trusted=trusted,
        encrypt=yes(kv.get("encrypt", "yes")), trust_server_certificate=yes(kv.get("trust_cert", "no")),
    )


def _guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def run(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except (DatabaseError, GuardError, ValueError, FileNotFoundError) as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    return run


def _say(msg: str) -> None:
    typer.echo(msg, err=True)


def _done(written: list[str], out: Path) -> None:
    typer.echo(f"Wrote to {out}:")
    for w in written:
        typer.echo(f"  {w}")


# ---- commands -------------------------------------------------------------------------------------
@app.command()
@_guarded
def inventory(
    server: Server = None, port: Port = 1433, database: Database = None, user: User = None, driver: Driver = "pymssql",
    trusted: Trusted = False, encrypt: Encrypt = True, trust_server_certificate: TrustCert = False,
    out: Out = Path("db-agent-sqlserver-output"), format: Format = "md,json,xlsx,html",
    databases: Annotated[Optional[str], typer.Option("--databases", help="Comma list of databases to read. Default: all user databases.")] = None,
    include_system: Annotated[bool, typer.Option("--include-system", help="Also read master, model, msdb, tempdb.")] = False,
    target: Annotated[int, typer.Option("--target", help="Upgrade target: 2019 or 2022.")] = 2022,
    code: Annotated[bool, typer.Option("--code/--no-code", help="Save stored procedure and function code to modules.json.")] = True,
) -> None:
    """Inventory a SQL Server: version, databases, objects and upgrade risks. Metadata only."""
    fmts = _formats(format)
    if target not in (2019, 2022):
        raise typer.BadParameter("--target must be 2019 or 2022")
    cfg = _config(server, port, database, user, driver, trusted, encrypt, trust_server_certificate)
    inv = inventory_mod.collect(cfg, [d.strip() for d in databases.split(",")] if databases else None, include_system, QueryGuard(), _say)
    risks = [r.as_dict() for r in risks_mod.assess(inv, 15 if target == 2019 else 16)]
    out.mkdir(parents=True, exist_ok=True)
    written = outputs.write_inventory(out, inv, risks, fmts, code)
    if "html" in fmts:
        written += report_mod.write_report(out)
    outputs.order_workbook(out)
    _done(written, out)
    by = {s: sum(1 for r in risks if r["severity"] == s) for s in ("high", "medium", "low")}
    typer.echo(f"{len(inv['objects'])} database(s) read. Risks: {by['high']} high, {by['medium']} medium, {by['low']} low.")
    if inv["warnings"]:
        typer.echo(f"{len(inv['warnings'])} item(s) could not be collected (see inventory.md).")


@app.command()
@_guarded
def ssrs(
    server: Server = None, port: Port = 1433, database: Database = None, user: User = None, driver: Driver = "pymssql",
    trusted: Trusted = False, encrypt: Encrypt = True, trust_server_certificate: TrustCert = False,
    out: Out = Path("db-agent-sqlserver-output"), format: Format = "md,json,xlsx,html",
    reportserver_db: Annotated[Optional[str], typer.Option("--reportserver-db", help="Name of the ReportServer database on --server.")] = None,
    rdl_folder: Annotated[Optional[Path], typer.Option("--rdl-folder", help="Folder with .rdl (and .rsd, .rds) files.")] = None,
    usage_csv: Annotated[Optional[Path], typer.Option("--usage-csv", help="Execution log exported to CSV (ExecutionLog3 columns).")] = None,
    subscriptions_csv: Annotated[Optional[Path], typer.Option("--subscriptions-csv", help="Folder mode: subscriptions (ItemPath, DeliveryExtension, DataDriven).")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", help="Classification rules YAML (see src/db_agent/engines/sqlserver/data/classification.yaml).")] = None,
    as_of: Annotated[Optional[str], typer.Option("--as-of", help="Count 90 and 365 days back from this date (YYYY-MM-DD). Default today.")] = None,
    data_database: Annotated[Optional[str], typer.Option("--data-database", help="Database the reports query. Used to list the tables behind stored procedures.")] = None,
) -> None:
    """Inventory SSRS reports and classify each as move, review or retire."""
    fmts = _formats(format)
    if bool(reportserver_db) == bool(rdl_folder):
        raise typer.BadParameter("give exactly one of --reportserver-db or --rdl-folder")
    cfg_rules = classify.load_config(config)
    when = datetime.strptime(as_of, "%Y-%m-%d") if as_of else None
    out.mkdir(parents=True, exist_ok=True)
    usage = usage_mod.load_csv(usage_csv, when) if usage_csv else None
    if rdl_folder:
        if not rdl_folder.is_dir():
            raise FileNotFoundError(f"folder not found: {rdl_folder}")
        res = ssrs_mod.from_folder(rdl_folder, usage, cfg_rules, subscriptions_csv)
    else:
        scfg = _config(server, port, reportserver_db, user, driver, trusted, encrypt, trust_server_certificate)
        with SafeConnection(scfg, QueryGuard()) as conn:
            if usage is None:
                try:
                    usage = usage_mod.load_executionlog(conn)
                except DatabaseError as exc:
                    _say(f"Usage not read from ExecutionLog3 ({str(exc)[:120]}). Reports will not be classified retire.")
            res = ssrs_mod.from_reportserver(conn, usage, cfg_rules)
    if data_database and server:
        _resolve_procs(res, _config(server, port, data_database, user, driver, trusted, encrypt, trust_server_certificate))
    written = outputs.write_ssrs(out, res, fmts)
    if "html" in fmts:
        written += report_mod.write_report(out)
    outputs.order_workbook(out)
    _done(written, out)
    c = res["classification_counts"]
    typer.echo(f"{res['report_count']} report(s): move {c.get('move', 0)}, review {c.get('review', 0)}, retire {c.get('retire', 0)}.")


def _resolve_procs(res: dict[str, Any], cfg: ConnectionConfig) -> None:
    q = "SELECT DISTINCT d.referenced_schema_name AS s, d.referenced_entity_name AS n FROM sys.sql_expression_dependencies d WHERE d.referencing_id = OBJECT_ID(%s)"
    with SafeConnection(cfg, QueryGuard()) as conn:
        for r in res["reports"]:
            for proc in r["procedures"]:
                try:
                    rows = conn.query(q, (proc,))
                except DatabaseError:
                    continue
                found = [f"{x['s'] + '.' if x['s'] else ''}{x['n']}" for x in rows if x["n"]]
                r["tables"] = sorted(set(r["tables"]) | set(found), key=str.lower)
    from collections import Counter

    res["table_usage"] = Counter(t for r in res["reports"] for t in r["tables"]).most_common()


@app.command()
@_guarded
def validate(
    source: Annotated[str, typer.Option("--source", help="server=HOST,database=DB,user=U[,port=1433,driver=pymssql,trusted=no,encrypt=yes,trust_cert=no]. Password: DBAGENT_SOURCE_PASSWORD.")],
    target: Annotated[str, typer.Option("--target", help="Same form. Password: DBAGENT_TARGET_PASSWORD.")],
    reports: Annotated[Path, typer.Option("--reports", help="reports.csv written by the ssrs command (reports.json must be next to it).")],
    params: Annotated[Optional[Path], typer.Option("--params", help="YAML with parameter values: defaults: {Name: value}; reports: {/path: {Name: value}}.")] = None,
    allow_exec: Annotated[bool, typer.Option("--allow-exec", help="Also run datasets that call stored procedures.")] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds per query.")] = 60,
    max_rows: Annotated[int, typer.Option("--max-rows", help="Skip a dataset that returns more rows than this.")] = 1_000_000,
    out: Out = Path("db-agent-sqlserver-output"), format: Format = "md,json,xlsx,html",
) -> None:
    """Run each 'move' report's queries on the old and new server and compare counts and totals."""
    fmts = _formats(format)
    src, tgt = parse_conn_spec(source, "DBAGENT_SOURCE", "--source"), parse_conn_spec(target, "DBAGENT_TARGET", "--target")
    src.query_timeout = tgt.query_timeout = timeout
    sidecar = reports.with_name("reports.json")
    if not sidecar.exists():
        raise FileNotFoundError(f"{sidecar} not found. Run `ssrs` first; validate needs the dataset queries it saved.")
    import json

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    overrides = yaml.safe_load(params.read_text()) if params else {}
    rows = validate_mod.validate_reports(data["reports"], src, tgt, overrides or {}, allow_exec, max_rows, _say)
    out.mkdir(parents=True, exist_ok=True)
    written = outputs.write_validation(out, rows, fmts)
    if (out / "inventory.json").exists() or (out / "reports.json").exists():
        pass
    if "html" in fmts and (out / "reports.json").exists():
        written += report_mod.write_report(out)
    outputs.order_workbook(out)
    _done(written, out)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    typer.echo("Validation: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to compare"))
    if counts.get("fail"):
        raise typer.Exit(2)


@app.command()
@_guarded
def report(out: Out = Path("db-agent-sqlserver-output")) -> None:
    """Combine inventory, ssrs and validate outputs found in --out into summary.html and the workbook."""
    if not out.is_dir() or not any((out / f).exists() for f in ("inventory.json", "reports.json", "validation.json")):
        raise FileNotFoundError(f"nothing to report in {out}. Run inventory, ssrs or validate first.")
    _done(report_mod.write_report(out), out)


@app.command()
def version() -> None:
    """Print the version and the SQL Server versions it supports."""
    typer.echo(f"db-agent {__version__}")
    typer.echo(f"Supported: {SUPPORTED_SQL_SERVER}")
    typer.echo("Python " + sys.version.split()[0])


if __name__ == "__main__":
    app()
