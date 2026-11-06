# What was tested, and what was not

Last run: 2026-11-06. `make test` (95 unit tests) and `make test-live` (adds 8 integration tests, plus 4 Azure tests when the Azure variables are set).

## Covered

- The query guard: 60+ allowed and blocked statements, including `SELECT INTO`, `OPENROWSET`, linked-server names, stacked statements, comments and strings hiding keywords, and `EXEC` rules. A test also proves no module except `db.py` imports a driver or touches a connection, and that the guard runs before every execute.
- RDL parsing on 12 sample reports and on every RDL namespace version; XML external entities are not resolved.
- SQL table extraction, classification rules (including the short-log rule), usage counts, risk rules on a hand-made inventory, `validate` comparison logic, parameter binding, and "no secrets in any output file".
- **Live, against Azure SQL Edge 15.0 (two containers) with AdventureWorks2017 restored:**
  - `inventory` reads the database; row counts match (Sales.SalesOrderHeader = 31,465); no statement touched a business table.
  - A login with only the grants in `docs/permissions.md` can run `inventory`, and cannot read business data or create a table.
  - `validate` passes for 4 datasets, including a stored procedure with `--allow-exec`, skips a multi-value parameter and a procedure without `--allow-exec`, detects a changed value on the target, and reports a table missing on the target as fail.
  - The ReportServer path (catalog, linked reports, subscriptions, ExecutionLog3) on a **stand-in** ReportServer database.

Bugs found by the live runs and fixed: views were missed (`sys.objects.type` is a padded `char(2)`); copying a linked report broke; the guard could not parse bound-parameter placeholders; two false positives (XML method calls reported as cross-database references; compatibility levels above 120 reported as deprecated).

## Real SQL Server 2017 and 2022 (tested 2026-11-06)

Docker Desktop on an Apple Silicon Mac with Rosetta emulation on (the plain QEMU emulation crashed the x86 images).
Engines: SQL Server 2017 CU31 (14.0.3560.2) as source and SQL Server 2022 CU27 (16.0.4305.5) as target, both Linux,
AdventureWorks2017 restored in each. The 8 live tests pass on them, and the full command line flow ran: `inventory`
(2017 detected, end-of-support flagged, full-text catalog found, compatibility 140 below 160), `ssrs` (5 move, 5 review,
2 retire), `validate` 2017 to 2022 (4 pass, 1 skipped), `report`. One change came out of it: runtime deprecated-feature
counters on a fresh server (`sp_addsrvrolemember`, `sp_dropsrvrolemember`, `sysobjects`, from the server's own setup) were
rated high; they are now medium. Emulated engines are slow to start and are not a performance benchmark.

## Real SQL Server 2016 and real SSRS 2016, on Windows (tested 2026-11-06)

An Azure Windows Server 2019 VM with SQL Server 2016 SP3 CU1 (13.0.7050.2, Developer) and SQL Server Reporting Services 2016
installed from the SQL media, AdventureWorks2016 restored. The 12 sample reports were deployed through the SSRS web service and
run through URL access, which produced a real `ExecutionLog3` (37 rows).

- `inventory` on 2016: version, end of support, Agent job (read from a real msdb), full-text catalog, compatibility level 130.
- `ssrs --reportserver-db` on the real catalog: all 12 reports read, run counts match what was executed (9, 7, 5, 4, 3, 3, 2, 2, 1, 1).
- Confirmed on a real server: `ExecutionLogDaysKept` defaults to 60, and `Catalog.Content` is raw XML.
- On Windows itself, with Python 3.12 and a wheel built on the Mac: ODBC Driver 17 with Windows authentication (no password) as the
  local SYSTEM account, `inventory`, `ssrs`, `report`. This is local NTLM, not a domain with Kerberos.

Bugs found and fixed: **10 of the 12 sample reports were rejected by real SSRS** (a dataset and its table had the same name; the
parser accepted them, SSRS does not), so the generator was fixed and all 12 now upload; the ODBC path was hard-wired to Driver 18
and failed on machines with 17 or 13 (now auto-detected, override with `DBAGENT_ODBC_DRIVER`); **the built wheel left out
`classification.yaml`** because a `.gitignore` rule for the backup folder matched the package data folder, which would have broken
every normal install (a test now checks the built wheel); a certificate error now says to use `--trust-server-certificate`.
Not exercised: a failed report counts as a run in the usage numbers (VendorSpend returned HTTP 500 and still shows 2 runs).

## Seeded legacy objects and scale (tested 2026-11-06)

`scripts/seed_risky_objects.py` creates a database full of legacy objects, and `tests/seeded/` checks each is reported. Run on
SQL Server 2017 (Linux) and on SQL Server 2016 (Windows, which adds a real CLR assembly compiled on the VM). Covered on real
engines: `text`/`ntext`/`image` columns, stand-alone defaults and rules, `SET ROWCOUNT`, `GROUP BY ALL`, MD5 hashing, table hints
without `WITH`, compatibility views, `DBCC DBREINDEX`, `SET FMTONLY`, `ANSI_NULLS OFF`, `xp_cmdshell`, encrypted modules,
cross-database references and synonyms, a synonym through a linked server, a linked server, Service Broker, a CLR assembly,
and an Agent job with deprecated code. A real SSRS subscription (file share, stored credentials) was read from the catalog and moved
its report from move to review.

Found and fixed: a reference to a name that is not a database on the server was silently dropped; it is now reported as
`cross-db-ref-unresolved` (low), because a dependency on a database that is not being moved is what a migration needs to know.
Also, run-command style output limits cost an hour, and SQL Agent does not restart on its own on the VM image; neither is a kit issue.

Scale, on a SQL Server 2017 container under emulation and a Mac laptop: 5,000 tables, 2,000 procedures, 1,000 views
(inventory 4.7 s, 188 MB peak) and 3,000 reports (`ssrs` 7.8 s, 226 MB; `report` 2.7 s, 150 MB). Output was 34 MB
and `summary.html` stayed 12 KB. Not measured: remote servers over a slow network, and real SSRS catalogs of that size.

## Not covered

- **Other SSRS versions.** Real SSRS was tested on 2016 only. 2017, 2019, 2022 and Power BI Report Server catalogs are unverified, and so are shared data sources, real subscriptions and encrypted-credential data sources.
- Sample reports were never opened in Report Builder or rendered.
- Domain (Kerberos) Windows authentication, Entra ID and Azure SQL Managed Instance.
- Very large datasets in `validate`, and installs well beyond the sizes below (tens of thousands of tables, 100+ databases).
- Packaging: PyPI, Docker image, Windows `.exe`.

## Azure SQL Database (tested 2026-11-06)

`inventory` was run against a real Azure SQL Database (free offer, serverless General Purpose, version 12.0.2000.8,
engine edition 5) holding AdventureWorksLT. It found three gaps that no local engine could show, now fixed:
`sys.master_files` does not exist (sizes now come from `sys.database_files` for the connected database), the platform's own
use of deprecated features showed up as 4 false "high" risks (counters are now skipped on Azure SQL Database), and
a doomed msdb login was attempted (now skipped, with an explanation). Run it with `tests/azure/` and the
`DBAGENT_TEST_AZURE_*` variables. Only the single-database case is covered; `ssrs` and `validate` were not run there.
