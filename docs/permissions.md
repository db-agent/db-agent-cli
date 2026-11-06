# Permissions your DBA needs to grant

The kit reads metadata and, for `validate` only, runs report queries. It never writes. Give it a
dedicated login so you can see exactly what it can do and remove it afterwards.

All grants below are read-only. Run the script as a member of `sysadmin`.

## 1. `inventory` and `ssrs` (required)

```sql
-- Server level
USE master;
CREATE LOGIN [dbagent_reader] WITH PASSWORD = '<choose a strong password>', CHECK_POLICY = ON;

-- Needed to read server and database settings, SQL Server Agent jobs, and the server's own
-- deprecated-feature counters. VIEW SERVER STATE is the only server-wide permission asked for:
-- it exposes the performance counters that show which deprecated features the instance really uses.
-- Without it the kit still works and reports that those counters were not collected.
GRANT VIEW ANY DEFINITION TO [dbagent_reader];
GRANT VIEW SERVER STATE   TO [dbagent_reader];   -- optional, see above
-- (SQL Server 2022 and later: you may grant VIEW SERVER PERFORMANCE STATE instead.)

-- SQL Server Agent jobs (optional; skipped if not granted)
USE msdb;
CREATE USER [dbagent_reader] FOR LOGIN [dbagent_reader];
GRANT SELECT ON dbo.sysjobs         TO [dbagent_reader];
GRANT SELECT ON dbo.sysjobsteps     TO [dbagent_reader];
GRANT SELECT ON dbo.sysjobschedules TO [dbagent_reader];
GRANT SELECT ON dbo.sysschedules    TO [dbagent_reader];
```

Repeat for **each business database** you want inventoried:

```sql
USE [YourBusinessDatabase];
CREATE USER [dbagent_reader] FOR LOGIN [dbagent_reader];
GRANT VIEW DEFINITION     TO [dbagent_reader];   -- object definitions and code
GRANT VIEW DATABASE STATE TO [dbagent_reader];   -- row counts from sys.dm_db_partition_stats
```

Neither grant gives access to table data. The kit never selects rows from business tables during
`inventory` or `ssrs`.

## 2. `ssrs --reportserver-db` (required for that mode)

```sql
USE [ReportServer];
CREATE USER [dbagent_reader] FOR LOGIN [dbagent_reader];
GRANT SELECT ON dbo.Catalog       TO [dbagent_reader];   -- report definitions (RDL)
GRANT SELECT ON dbo.Subscriptions TO [dbagent_reader];
GRANT SELECT ON dbo.ExecutionLog3 TO [dbagent_reader];   -- usage: counts only; user names are counted, not stored
```

`ExecutionLog3` is a view in the ReportServer database. The `ReportServerTempDB` database is not read.

If you cannot grant access to the ReportServer database, export the reports as `.rdl` files and use
`--rdl-folder`, and export the log with the query in [ssrs-inventory.md](ssrs-inventory.md) for `--usage-csv`.

## 3. `validate` (optional, only for this command)

`validate` runs each "move" report's dataset queries on the old and new server and keeps only row
counts and totals of numeric columns. It needs read access to the data those reports read.

```sql
USE [YourBusinessDatabase];
-- Easiest: read access to the whole database, on BOTH the source and the target.
ALTER ROLE db_datareader ADD MEMBER [dbagent_reader];
-- Tighter: grant SELECT only on the schemas or tables the reports use (see reports.csv, column "tables").
-- Only if you use --allow-exec (reports that call stored procedures):
-- GRANT EXECUTE ON OBJECT::dbo.YourProcedure TO [dbagent_reader];
```

`--allow-exec` runs stored procedures. A procedure can write. Only grant EXECUTE on procedures you
have read and know to be read-only.

## Safeguards in the tool

- Every statement passes one guard. Only a single `SELECT` (or `WITH ... SELECT`) is allowed, or `EXEC` of a stored procedure you allowed with `--allow-exec`. `SELECT ... INTO`, `OPENROWSET`, `OPENQUERY`, linked-server names and anything the guard cannot parse are refused.
- The connection asks for read-only intent (`ApplicationIntent=ReadOnly`) and rolls back after every query.
- The password is read from `DBAGENT_SQL_PASSWORD` or a hidden prompt. It is not accepted as a command-line option, and it is removed from error messages.
- The guard is a second line of defence. The login's permissions are the first. Do not give this login more than the above.

## Remove it afterwards

```sql
USE master;
DROP LOGIN [dbagent_reader];
-- and DROP USER [dbagent_reader] in each database you granted it in.
```
