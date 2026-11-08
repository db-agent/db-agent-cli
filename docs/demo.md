# Demo with live databases

`make demo-files` needs no database. This page is for the full demo: `inventory` and `validate` against
two real servers, with the AdventureWorks sample database.

## Intel Mac, Linux, Windows (x86)

```bash
docker compose up -d                       # SQL Server 2017 on :14331 (source), SQL Server 2022 on :14332 (target)
python scripts/restore_adventureworks.py   # downloads AdventureWorks2017.bak (about 48 MB) and restores it into both
make demo
```

## Apple Silicon

The x86 SQL Server images crash under Docker Desktop's default emulation (we saw a segmentation fault in
QEMU). Two options:

1. In Docker Desktop, turn on **Use Rosetta for x86/amd64 emulation**, restart it, and use the commands above. Tested: SQL Server 2017 and 2022 both start and run the full demo this way.
2. Use Azure SQL Edge, which runs natively on ARM. This is what the tests were run with:
   ```bash
   docker compose -f docker-compose.edge.yml up -d
   python scripts/restore_adventureworks.py
   make demo
   ```
   Azure SQL Edge is a smaller engine with the SQL Server 2019 core (version 15.x), no SQL Server Agent and no CLR.
   Microsoft retired it in September 2025. Use it for the queries and `validate`, not to judge a 2016 or 2017 upgrade.

The demo password (`DbAgent_Demo_2017!`) is for these local containers only.

## What you should see

- `inventory`: one database, about 70 tables, views, procedures, and a list of risks (full-text catalog, compatibility level, cross-database references and so on).
- `ssrs`: 12 reports: 5 move, 5 review, 2 retire.
- `validate`: 4 pass, 1 skipped (a multi-value parameter, with the reason).
- `report`: `summary.html` and `db-agent-sqlserver.xlsx`.

To see the "log too short" behaviour, run `ssrs` with `--usage-csv examples/usage_60days.csv`. No report is called retire.
