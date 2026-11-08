"""All SQL the health check sends. Each statement starts with a marker comment, which also makes the tool's
queries easy to find in query history. Values that go into SQL are validated here, never passed through raw."""

from __future__ import annotations

import re
from datetime import date

_ID = re.compile(r"^\d+$")

# DBU list price in force when the usage record ended. List price: before any negotiated discount.
_PRICED = """
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON p.sku_name = u.sku_name AND p.usage_unit = u.usage_unit
 AND u.usage_end_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_end_time < p.price_end_time)
"""
_COST = "u.usage_quantity * p.pricing.effective_list.default"


def _ws(workspace_id: str | None) -> str:
    if workspace_id is None:
        return ""
    if not _ID.match(workspace_id):
        raise ValueError("workspace id must be numeric")
    return f"AND u.workspace_id = '{workspace_id}'"


def cost_by_month(start: date, end_exclusive: date, workspace_id: str | None) -> str:
    return f"""/* dbagent:cost_by_month */
SELECT date_trunc('month', u.usage_date) AS month, u.sku_name,
       sum(u.usage_quantity) AS dbus, sum({_COST}) AS cost, count(DISTINCT u.usage_date) AS days
{_PRICED}
WHERE u.usage_date >= DATE'{start.isoformat()}' AND u.usage_date < DATE'{end_exclusive.isoformat()}'
  AND u.usage_unit = 'DBU' {_ws(workspace_id)}
GROUP BY 1, 2 ORDER BY 1, 2"""


def spend_by_cluster(days: int, workspace_id: str | None) -> str:
    return f"""/* dbagent:spend_by_cluster */
SELECT u.usage_metadata.cluster_id AS cluster_id, sum(u.usage_quantity) AS dbus, sum({_COST}) AS cost
{_PRICED}
WHERE u.usage_date >= date_sub(current_date(), {int(days)}) AND u.usage_metadata.cluster_id IS NOT NULL
  AND u.billing_origin_product IN ('ALL_PURPOSE', 'INTERACTIVE') AND u.usage_unit = 'DBU' {_ws(workspace_id)}
GROUP BY 1"""


def spend_by_warehouse(days: int, workspace_id: str | None) -> str:
    return f"""/* dbagent:spend_by_warehouse */
SELECT u.usage_metadata.warehouse_id AS warehouse_id, sum(u.usage_quantity) AS dbus, sum({_COST}) AS cost
{_PRICED}
WHERE u.usage_date >= date_sub(current_date(), {int(days)}) AND u.usage_metadata.warehouse_id IS NOT NULL
  AND u.usage_unit = 'DBU' {_ws(workspace_id)}
GROUP BY 1"""


def queries_by_warehouse(days: int, workspace_id: str | None) -> str:
    w = f"AND workspace_id = '{workspace_id}'" if workspace_id else ""
    if workspace_id and not _ID.match(workspace_id):
        raise ValueError("workspace id must be numeric")
    return f"""/* dbagent:queries_by_warehouse */
SELECT compute.warehouse_id AS warehouse_id, count(*) AS queries, max(start_time) AS last_query
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL {int(days)} DAYS AND compute.type = 'WAREHOUSE' {w}
GROUP BY 1"""


def untagged_spend(days: int, workspace_id: str | None) -> str:
    return f"""/* dbagent:untagged_spend */
SELECT u.billing_origin_product AS product,
       sum({_COST}) AS cost,
       sum(CASE WHEN u.custom_tags IS NULL OR cardinality(u.custom_tags) = 0 THEN {_COST} END) AS untagged_cost,
       sum(u.usage_quantity) AS dbus,
       sum(CASE WHEN u.custom_tags IS NULL OR cardinality(u.custom_tags) = 0 THEN u.usage_quantity END) AS untagged_dbus
{_PRICED}
WHERE u.usage_date >= date_sub(current_date(), {int(days)}) AND u.usage_unit = 'DBU' {_ws(workspace_id)}
GROUP BY 1 ORDER BY 3 DESC"""


def dbfs_mount_events(days: int, workspace_id: str | None) -> str:
    w = f"AND workspace_id = '{workspace_id}'" if workspace_id else ""
    if workspace_id and not _ID.match(workspace_id):
        raise ValueError("workspace id must be numeric")
    return f"""/* dbagent:dbfs_mount_events */
SELECT event_date, user_identity.email AS user_email, to_json(request_params) AS params
FROM system.access.audit
WHERE service_name = 'dbfs' AND action_name = 'mount' AND event_date >= date_sub(current_date(), {int(days)}) {w}
ORDER BY event_date DESC LIMIT 500"""


SHOW_SCHEMAS = "/* dbagent:hms_schemas */ SHOW SCHEMAS IN hive_metastore"


def show_tables(schema: str) -> str:
    if not re.match(r"^[\w\-]+$", schema):
        raise ValueError(f"odd schema name {schema!r}")
    return f"/* dbagent:hms_tables */ SHOW TABLES IN hive_metastore.`{schema}`"
