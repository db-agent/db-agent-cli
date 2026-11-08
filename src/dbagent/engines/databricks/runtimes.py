"""Databricks Runtime support dates.

Source: https://docs.databricks.com/aws/en/release-notes/runtime/eos (ended versions) and
https://docs.databricks.com/aws/en/release-notes/runtime/ (supported versions), read on 2026-11-07.
Update EOS when Databricks publishes new versions. Versions newer than the newest one listed here are
reported as "unknown", never as supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

EOS: dict[str, date] = {
    "10.5": date(2022, 11, 4), "11.0": date(2022, 12, 16), "11.1": date(2023, 1, 27), "11.2": date(2023, 3, 7),
    "11.3": date(2025, 10, 19), "12.0": date(2023, 6, 15), "12.1": date(2023, 7, 18), "12.2": date(2026, 3, 1),
    "13.0": date(2023, 10, 14), "13.1": date(2023, 12, 1), "13.2": date(2024, 1, 6), "13.3": date(2026, 8, 22),
    "14.0": date(2024, 3, 11), "14.1": date(2025, 2, 12), "14.2": date(2024, 11, 5), "14.3": date(2027, 2, 1),
    "15.0": date(2024, 5, 31), "15.1": date(2024, 10, 30), "15.2": date(2025, 1, 7), "15.3": date(2025, 1, 7),
    "15.4": date(2027, 8, 19), "16.0": date(2025, 5, 11), "16.1": date(2025, 6, 20), "16.2": date(2025, 8, 5),
    "16.3": date(2025, 9, 30), "16.4": date(2028, 5, 9), "17.0": date(2025, 12, 23), "17.1": date(2026, 2, 1),
    "17.2": date(2026, 3, 16), "17.3": date(2028, 10, 22), "18.0": date(2026, 7, 15), "18.1": date(2026, 9, 11),
    "18.2": date(2026, 11, 5),
}
LTS = {"11.3", "12.2", "13.3", "14.3", "15.4", "16.4", "17.3"}
NEWEST_KNOWN = (18, 2)
SOURCE = "https://docs.databricks.com/aws/en/release-notes/runtime/eos"

_VER = re.compile(r"^(\d+)\.(\d+)")


@dataclass(frozen=True)
class RuntimeStatus:
    spark_version: str
    label: str            # "13.3 LTS"
    eos: date | None
    state: str            # ended | ending_soon | supported | unknown
    days_left: int | None


def classify(spark_version: str | None, today: date, warn_days: int = 180) -> RuntimeStatus:
    sv = spark_version or ""
    if sv.startswith("apache-spark-"):
        return RuntimeStatus(sv, sv, None, "ended", None)   # Spark 2.x / 3.0 era images
    m = _VER.match(sv)
    if not m:
        return RuntimeStatus(sv, sv or "unknown", None, "unknown", None)   # custom images, empty
    key = f"{int(m.group(1))}.{int(m.group(2))}"
    label = key + (" LTS" if key in LTS else "")
    eos = EOS.get(key)
    if eos is None:
        if (int(m.group(1)), int(m.group(2))) < (10, 5):
            return RuntimeStatus(sv, label, None, "ended", None)   # 10.4 LTS and older are end-of-life
        return RuntimeStatus(sv, label, None, "unknown", None)     # newer than this table: check the docs
    left = (eos - today).days
    state = "ended" if left < 0 else "ending_soon" if left <= warn_days else "supported"
    return RuntimeStatus(sv, label, eos, state, left)
