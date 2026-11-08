"""Report usage from the SSRS execution log (ExecutionLog3) or an exported CSV.

Only counts are kept. User names are used to count distinct users and are then dropped.

Important: SSRS keeps the execution log for 60 days by default (ExecutionLogDaysKept). A report
that runs once a year looks unused in a 60-day log. So every result carries the log window, and
the classifier refuses to say "retire" unless the window covers the retire period.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .db import SafeConnection

Q_USAGE = """
SELECT ItemPath,
       MAX(TimeStart) AS last_run,
       SUM(CASE WHEN TimeStart >= DATEADD(day, -90, GETDATE()) THEN 1 ELSE 0 END) AS runs_90,
       SUM(CASE WHEN TimeStart >= DATEADD(day, -365, GETDATE()) THEN 1 ELSE 0 END) AS runs_365,
       COUNT(DISTINCT UserName) AS distinct_users,
       AVG(CAST(DATEDIFF(millisecond, TimeStart, TimeEnd) AS bigint)) AS avg_ms,
       SUM(CASE WHEN Status <> 'rsSuccess' THEN 1 ELSE 0 END) AS errors
FROM dbo.ExecutionLog3
WHERE RequestType IN ('Interactive', 'Subscription')
GROUP BY ItemPath
"""
Q_WINDOW = "SELECT MIN(TimeStart) AS oldest, MAX(TimeStart) AS newest, COUNT(*) AS entries FROM dbo.ExecutionLog3"


@dataclass
class ReportUsage:
    last_run: datetime | None = None
    runs_90: int = 0
    runs_365: int = 0
    distinct_users: int = 0
    avg_ms: int | None = None
    errors: int = 0


@dataclass
class UsageData:
    by_path: dict[str, ReportUsage]
    oldest: datetime | None
    newest: datetime | None
    as_of: datetime
    source: str  # "executionlog3" | "csv"

    @property
    def window_days(self) -> int:
        """How many days back the log reaches. 0 when the log is empty."""
        if self.oldest is None:
            return 0
        return max(0, (self.as_of - self.oldest).days)

    def get(self, path: str) -> ReportUsage:
        return self.by_path.get(path.lower(), ReportUsage())


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:26] if "." in value else value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def load_csv(path: Path, as_of: datetime | None = None) -> UsageData:
    """Columns follow ExecutionLog3: ItemPath, UserName, TimeStart, TimeEnd, Status, RequestType.
    Only ItemPath and TimeStart are required."""
    as_of = as_of or datetime.now()
    rows: dict[str, list[dict]] = {}
    oldest = newest = None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = {h.lower(): h for h in (reader.fieldnames or [])}
        for need in ("itempath", "timestart"):
            if need not in header:
                raise ValueError(f"usage CSV needs a column named {'ItemPath' if need == 'itempath' else 'TimeStart'}")
        for r in reader:
            lr = {k.lower(): v for k, v in r.items()}
            rtype = (lr.get("requesttype") or "Interactive").strip()
            ts = _parse_dt(lr.get("timestart", ""))
            if ts is None:
                continue
            oldest = ts if oldest is None or ts < oldest else oldest
            newest = ts if newest is None or ts > newest else newest
            if rtype not in ("Interactive", "Subscription"):
                continue
            rows.setdefault((lr.get("itempath") or "").strip().lower(), []).append(
                {"ts": ts, "end": _parse_dt(lr.get("timeend", "")), "user": lr.get("username") or "", "status": (lr.get("status") or "rsSuccess").strip()}
            )
    by_path: dict[str, ReportUsage] = {}
    d90, d365 = as_of - timedelta(days=90), as_of - timedelta(days=365)
    for key, items in rows.items():
        durations = [(i["end"] - i["ts"]).total_seconds() * 1000 for i in items if i["end"]]
        by_path[key] = ReportUsage(
            last_run=max(i["ts"] for i in items),
            runs_90=sum(1 for i in items if i["ts"] >= d90),
            runs_365=sum(1 for i in items if i["ts"] >= d365),
            distinct_users=len({i["user"] for i in items if i["user"]}),
            avg_ms=int(sum(durations) / len(durations)) if durations else None,
            errors=sum(1 for i in items if i["status"] != "rsSuccess"),
        )
    return UsageData(by_path, oldest, newest, as_of, "csv")


def load_executionlog(conn: SafeConnection) -> UsageData:
    win = conn.query(Q_WINDOW)[0]
    by_path: dict[str, ReportUsage] = {}
    for r in conn.query(Q_USAGE):
        by_path[(r["ItemPath"] or "").lower()] = ReportUsage(
            last_run=r["last_run"], runs_90=int(r["runs_90"] or 0), runs_365=int(r["runs_365"] or 0),
            distinct_users=int(r["distinct_users"] or 0), avg_ms=int(r["avg_ms"]) if r["avg_ms"] is not None else None,
            errors=int(r["errors"] or 0),
        )
    return UsageData(by_path, win["oldest"], win["newest"], datetime.now(), "executionlog3")
