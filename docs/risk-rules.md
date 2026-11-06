# Upgrade risk rules

Where the rules come from, so you can check them.

Source pages, fetched 2026-11-05:

- [Deprecated Database Engine Features in SQL Server 2016](https://learn.microsoft.com/en-us/previous-versions/sql/database-engine/deprecated-database-engine-features-in-sql-server-2016)
  (the cumulative baseline: features deprecated in earlier releases stay deprecated)
- [Discontinued Database Engine Functionality in SQL Server](https://learn.microsoft.com/en-us/sql/database-engine/discontinued-database-engine-functionality-in-sql-server)
- [ALTER DATABASE compatibility level](https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-database-transact-sql-compatibility-level)
- [Azure SQL Database T-SQL differences from SQL Server](https://learn.microsoft.com/en-us/azure/azure-sql/database/transact-sql-tsql-differences-sql-server)
  (cited for the Azure SQL Database flags; not re-read when the rules were written, so check it)

The pages for SQL Server 2017, 2019 and 2022 add a few items (for example Distributed Replay and Machine
Learning Server). They cannot be seen from object code and are not rules here.

## What is checked

| Where | Check |
|---|---|
| Server | The server's own `Deprecated Features` performance counters. A count above zero means the feature was really used since the last restart (needs `VIEW SERVER STATE`). |
| Databases | Compatibility level 100 or lower (deprecated); below the upgrade target; replication; `DQS_MAIN` (Data Quality Services). |
| Tables | `text`, `ntext`, `image` columns; Stretch Database (discontinued July 2024); stand-alone `CREATE DEFAULT` and `CREATE RULE` objects; FILESTREAM. |
| Server objects | Database mirroring; SOAP endpoints; linked servers (and the `SQLOLEDB` provider). |
| Cross references | References through a linked server; references to another database; synonyms that do the same. |
| Features | CLR assemblies, Service Broker queues, full-text catalogs, SQL Server Agent jobs, `xp_cmdshell`. |
| Module code (procedures, functions, views, triggers) and Agent T-SQL steps | See below. |
| Modules | Stored with `ANSI_NULLS OFF`; encrypted (code cannot be read, reported as a coverage gap). |

### Code patterns

`SET ROWCOUNT`, `SET FMTONLY`, `GROUP BY ALL`, `SET ANSI_NULLS/ANSI_PADDING/CONCAT_NULL_YIELDS_NULL OFF`,
`SET OFFSETS`, table hints without `WITH`, `READTEXT/WRITETEXT/UPDATETEXT/TEXTPTR/TEXTVALID`,
`DBCC DBREINDEX/INDEXDEFRAG/SHOWCONTIG/PINTABLE`, `fn_get_sql`, `USER_ID()`, `PERMISSIONS()`,
`INDEXKEY_PROPERTY`, `::fn_` calls, `SETUSER`, `GRANT/DENY/REVOKE ALL`, RC4/DESX encryption,
MD2/MD4/MD5/SHA/SHA1 in `HASHBYTES`, `XMLDATA`, deprecated catalog and SQL Trace views, the old
compatibility views (`sysobjects`, `syscolumns` and the rest of Microsoft's list), `IDENTITYCOL`,
`ROWGUIDCOL`, `CREATE DEFAULT/RULE`, `SQLOLEDB`, and about sixty deprecated system stored procedures
(`sp_addlogin`, `sp_adduser`, `sp_password`, `sp_depends`, `sp_dbcmptlevel`, `sp_attach_db`, `sp_lock`,
the SQL Trace procedures, and others). The exact list is in `src/dbagent_sqlserver/rules.py`.

### Limits

- Strings and comments are ignored, so **dynamic SQL held in a string is not scanned**.
- Matching is by text, not by a full T-SQL parser. Expect a few false positives (for example a column
  that happens to be named `sysobjects`) and treat each finding as "look here".
- Encrypted modules cannot be read.
- Rules about Azure SQL Database are flagged `azure_sql_database_only` and mostly do not apply to Azure
  SQL Managed Instance or SQL Server 2022.
