# Changelog

## 0.2.0

First release.

### Added

- One `db-agent` command with a command group per engine: `db-agent <engine> <verb>`. `db-agent engines` lists the engines and `db-agent version` prints the version.
- `sqlserver` engine, for SQL Server 2016/2017 upgrades and SSRS to Power BI moves:
  - `inventory`: version, edition, databases, tables, row counts from metadata, views, procedures, linked servers and Agent jobs, with upgrade risks and a link to the Microsoft page for each.
  - `ssrs`: every report with its data sources, datasets, parameters, subreports, subscriptions and usage. Each report is classified as move, review or retire, with the reason. It reads the ReportServer database or a folder of `.rdl` files.
  - `validate`: runs each "move" report's queries on the old and new server and compares row counts and totals.
  - `report`: combines the outputs into `summary.html` and an Excel workbook. Output formats are md, json, xlsx and html.
  - Windows authentication through ODBC (`--driver odbc`, optional `odbc` extra).
- `databricks` engine, `health` command: runtimes past or near end of support, legacy dependencies (Hive metastore tables, DBFS mount events, init scripts on DBFS, clusters without a Unity Catalog access mode), DBU spend by month at list price, and spend waste (scheduled jobs on all-purpose clusters, clusters without auto-termination, idle SQL warehouses, untagged spend).
- Read-only guards:
  - SQL Server: one statement only, a `SELECT` or `WITH ... SELECT`, or a stored procedure you name with `--allow-exec`. Anything else is refused, including `SELECT INTO`, `OPENROWSET` and linked-server names. A test checks that no module except `db.py` touches a driver.
  - Databricks: REST list and get calls only. SQL is limited to `SELECT`/`WITH` and `SHOW ... IN hive_metastore`. A test checks that only `client.py` imports the SDK.
  - Permissions for a least-privilege SQL Server login are in `docs/permissions.md`.
- Sample data in `examples/`: 12 sample reports and a synthetic usage log, for a demo that needs no database (`make demo-files`).

### Changed

- The project started as the single-engine `db-agent-sqlserver` package. It is now the `db_agent` package with engines under `db_agent.engines`. The `db-agent-sqlserver` command still works.
- The repository is named `db-agent-kit`.

### Testing

What was tested, and what was not, is in `docs/testing.md` and `docs/databricks-health.md`. The Databricks engine was live-tested on one small AWS workspace only.
