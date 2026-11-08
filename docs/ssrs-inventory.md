# SSRS inventory

`db-agent sqlserver ssrs` lists every SSRS report, says what each one uses, and classifies it as
**move**, **review** or **retire** before you move to Power BI.

## What it reads

Two ways in. Use one.

| Mode | Option | Reads |
|---|---|---|
| ReportServer database | `--reportserver-db ReportServer` | `dbo.Catalog` (report, shared dataset and data source definitions, linked reports), `dbo.Subscriptions` (counts), `dbo.ExecutionLog3` (usage counts) |
| Folder of files | `--rdl-folder ./reports` | `.rdl` reports, plus `.rsd` shared datasets and `.rds` shared data sources if they are in the folder |

Optional extras:

- `--usage-csv log.csv` usage for folder mode (or to replace the database log). Columns follow
  `ExecutionLog3`: `ItemPath, UserName, TimeStart, TimeEnd, Status, RequestType`. Only `ItemPath` and
  `TimeStart` are required. Export it with:
  ```sql
  SELECT ItemPath, UserName, TimeStart, TimeEnd, Status, RequestType FROM dbo.ExecutionLog3;
  ```
- `subscriptions.csv` in the folder (or `--subscriptions-csv`): `ItemPath, DeliveryExtension, DataDriven`.
  Folder mode cannot see subscriptions otherwise, and says so.
- `--data-database SalesDb` to list the tables behind stored procedures a report calls
  (uses `sys.sql_expression_dependencies`, one level deep).
- `--config rules.yaml` to change the classification rules. The defaults are in
  `src/db_agent/engines/sqlserver/data/classification.yaml`.
- `--as-of 2026-11-01` to count the 90 and 365 day windows from a date other than today.

It reads RDL versions 2005, 2008, 2010 and 2016 (namespaces are matched by element name).

## What it does not read or keep

- No business data. No report is run.
- Connection strings are reduced to server and database name. A password found in one is never kept:
  only a flag "contains a password" is.
- User names from the log are counted, not stored. The `Parameters` column of the log is never read.
- Nothing leaves the machine.

## What it extracts for each report

Name, folder, path, RDL version, data sources (server and database only), every dataset (SQL text or
stored procedure name and parameters), report parameters (type, default, multi-value), subreports,
drillthrough targets, custom code (line count), custom assemblies, embedded images, number of
expressions and how many call custom code or `Lookup`, layout (tables, charts, gauges, maps, toggles),
subscriptions, and usage.

**Tables and procedures** come from parsing the dataset SQL (sqlglot, T-SQL). It is best effort. Each
report gets a confidence: `high` (every statement parsed), `medium` (some read by a text fallback),
`low` (the parser failed, names come from a text fallback and may be incomplete), `n/a` (stored
procedure only, an expression, or a non-SQL source such as Analysis Services).

## Classification

In this order:

1. **retire**: no runs in the last 365 days **and the usage log reaches back at least 365 days**.
2. **review**: any of: custom code, custom assemblies, subreports, drillthrough, a data-driven or
   non-email subscription, a non-SQL data source, a shared dataset or data source that was not supplied,
   SQL that could not be fully parsed, more than 150 expressions, fewer than 6 runs in 365 days, or
   more than half the runs failing.
3. **move**: everything else.

### The usage log is usually too short

SSRS keeps the execution log for **60 days by default** (`ExecutionLogDaysKept`; -1 keeps everything).
A report that runs once a quarter or once a year looks unused in a 60-day log. So the kit records how far
back the log reaches. If it is shorter than 365 days, **no report is called retire**. A report with no
runs in a short log is `review`, with the reason "the log covers only N days". With no usage data at all,
nothing is called retire.

To get a proper answer: raise `ExecutionLogDaysKept` now, and run again in a year, or supply an older
export of the log.

### Layout hint

Each report also gets a hint, `paginated` or `interactive`. It is a hint only. Reports that are
tabular and have page headers or footers suit Power BI paginated reports. Reports built on charts,
gauges, maps or show/hide toggles are more likely to need an interactive rebuild.

## Output

| File | Contents |
|---|---|
| `summary.md` | One page for a manager |
| `reports.csv` | One row per report |
| `reports.json` | Everything, including dataset SQL (read by `validate`) |
| `report-details/*.md` | One file per report: SQL, parameters, data sources, reasons |
| `db-agent-sqlserver.xlsx` | Reports tab (and the others when you run `report`) |
| `summary.html` | Printable summary |

## Not verified against a real SSRS server

The ReportServer queries were run against a stand-in database with the columns they read, not a real
SSRS catalog. Check the first run against your own server, and tell us what differs. The `Catalog.Type`
values used are 2 (report), 4 (linked report), 5 (data source) and 8 (shared dataset).
