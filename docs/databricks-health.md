# db-agent databricks health

A read-only check of one Databricks workspace. It uses the REST API and system tables, changes nothing, and
writes `databricks-health.md` and `databricks-health.json`.

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com     # or set DATABRICKS_HOST and DATABRICKS_TOKEN
db-agent databricks health                                  # uses a SQL warehouse that is already running
db-agent databricks health --warehouse-id <id> --profile <name>
db-agent databricks health --no-sql                         # REST checks only, no warehouse needed
db-agent databricks health --cost-from 2026-09 --cost-to 2026-11 --lookback-days 30
```

Tokens are never accepted on the command line. A **stopped** warehouse starts when the tool uses it and bills
while it runs; the tool never starts one on its own (it only picks a warehouse that is already running).
Every query starts with a `/* dbagent:... */` comment, so you can find them in query history.

## What it checks

| Group | Check | Source | Severity |
|---|---|---|---|
| Runtimes | Cluster and job-cluster definitions on a runtime past end of support | REST: clusters, jobs | high |
| | ... that ends support within `--eos-warning-days` (default 180) | REST | medium |
| Legacy | Init scripts stored on DBFS | REST | high |
| | No-isolation and legacy access modes | REST | medium |
| | Hive metastore tables (count per schema) | `SHOW SCHEMAS/TABLES IN hive_metastore` | medium |
| | DBFS mount creation events, last 365 days | `system.access.audit` | medium |
| Cost | DBU and list-price spend per month, volume vs price split, tier change | `system.billing.usage`, `list_prices` | info, medium if cost per day moves 20% or more |
| Waste | Scheduled jobs on all-purpose clusters | REST jobs plus cluster spend | high |
| | All-purpose clusters with auto-termination off | REST plus cluster spend | high if running, else medium |
| | SQL warehouses billed but with zero queries | billing plus `system.query.history` | high |
| | SQL warehouses with auto-stop off | REST | medium |
| | Spend with no tags | `system.billing.usage.custom_tags` | low under 20%, medium 20 to 49%, high 50% or more |

## What has been tested

- **Live:** one small AWS workspace (no all-purpose clusters, one job, one serverless warehouse, Unity Catalog only, all usage on the
  PREMIUM tier). The billing, query-history and audit queries, the cost comparison, the untagged-spend check and the
  "Hive metastore disabled" case ran against real system tables.
- **Not live:** the runtime, init-script, access-mode, jobs-on-all-purpose, auto-termination and idle-warehouse checks have only been run against
  fake data and objects built with the Databricks SDK's own classes. A workspace with a Standard to Premium switch has not been seen.
  Treat their first run on a real estate as a test.

## Limits you should know

- **Prices are list prices** (`pricing.effective_list.default`), before any discount. Use them to compare periods, not to reconcile an invoice.
- **Volume/price split** ("volume" includes a shift between SKUs) compares each month with the first month that has data, per day, so a partial current month is comparable. If the earlier months have no rows (system tables not enabled yet), the comparison starts later and says so.
- **Jobs on all-purpose compute:** billing does not record `job_id` for them, so the spend shown is the whole cluster's, including anything else that ran on it.
- **DBFS mounts:** audit logs show mount *creation* events only. They do not list mounts that exist today; `dbutils.fs.mounts()` in a notebook does. The audit action name (`service_name = 'dbfs'`, `action_name = 'mount'`) is taken from a community detection rule, not from Databricks reference documentation. If no events come back, that is not proof there are no mounts.
- **Hive metastore:** counted with `SHOW TABLES`, because `information_schema` does not cover it. A workspace with the legacy metastore disabled reports the error under "not collected".
- **Delta Live Tables / pipeline clusters and serverless** are not examined for runtimes.
- **Runtime dates** are a table in `runtimes.py`, read from Databricks' end-of-support page on 2026-11-07. A version newer than the table is reported as "unknown", never as supported. Update the table when Databricks releases new versions.
- A check that cannot run (missing permission, table not enabled) is listed under **Not collected**. The other checks still run, and the report never claims a clean result for a check that did not run.

## Access needed

- Workspace: read access to clusters, jobs and SQL warehouses (workspace admin sees all of them; otherwise only what the identity can view).
- `CAN USE` on one SQL warehouse.
- `SELECT` on `system.billing.usage`, `system.billing.list_prices`, `system.query.history`, `system.access.audit` (a metastore admin can grant it; billing and audit tables are account-wide, so the tool filters to this workspace unless you pass `--all-workspaces`).
