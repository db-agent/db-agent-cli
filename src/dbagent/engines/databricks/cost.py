"""Month-over-month DBU spend and a split of the change into volume and price."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

TIERS = ("STANDARD", "PREMIUM", "ENTERPRISE")


def month_list(start: str, end: str) -> list[date]:
    """'2026-09' .. '2026-11' -> first days of each month."""
    def p(s: str) -> date:
        y, m = s.split("-")
        return date(int(y), int(m), 1)
    a, b = p(start), p(end)
    if b < a:
        raise ValueError("--cost-to is before --cost-from")
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    if len(out) > 24:
        raise ValueError("at most 24 months")
    return out


def default_range(today: date, n: int = 3) -> tuple[str, str]:
    y, m = today.year, today.month - (n - 1)
    while m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}", f"{today.year:04d}-{today.month:02d}"


def tier_of(sku: str) -> str:
    head = sku.split("_", 1)[0]
    return head if head in TIERS else "OTHER"


def normalized(sku: str) -> str:
    return sku.split("_", 1)[1] if tier_of(sku) != "OTHER" else sku


def _f(v) -> float | None:
    return None if v is None else float(v)


def analyze(rows: list[dict], today: date) -> dict:
    """rows: month, sku_name, dbus, cost, days. Returns per-month figures and a volume/price split per day,
    measured against the first month that has data."""
    by_month: dict[str, dict] = {}
    for r in rows:
        key = str(r["month"])[:7]
        m = by_month.setdefault(key, {"month": key, "dbus": 0.0, "cost": 0.0, "unpriced_dbus": 0.0, "days": 0,
                                      "tiers": {}, "skus": {}})
        dbus, cost = float(r["dbus"] or 0), _f(r.get("cost"))
        m["dbus"] += dbus
        m["days"] = max(m["days"], int(r.get("days") or 0))
        if cost is None:
            m["unpriced_dbus"] += dbus
        else:
            m["cost"] += cost
        t = tier_of(r["sku_name"])
        m["tiers"][t] = m["tiers"].get(t, 0.0) + dbus
        n = normalized(r["sku_name"])
        s = m["skus"].setdefault(n, {"dbus": 0.0, "cost": 0.0})
        s["dbus"] += dbus
        s["cost"] += cost or 0.0
    months = [by_month[k] for k in sorted(by_month)]
    for m in months:
        y, mo = map(int, m["month"].split("-"))
        m["partial"] = (y, mo) == (today.year, today.month)
        d = max(m["days"], 1)
        m["dbus_per_day"] = m["dbus"] / d
        m["cost_per_day"] = m["cost"] / d
        m["rate"] = (m["cost"] / (m["dbus"] - m["unpriced_dbus"])) if m["dbus"] - m["unpriced_dbus"] > 0 else None
        m["dominant_tier"] = max(m["tiers"], key=m["tiers"].get) if m["tiers"] else None
    if months:
        base = months[0]
        base_rate = {k: v["cost"] / v["dbus"] for k, v in base["skus"].items() if v["dbus"] > 0 and v["cost"] > 0}
        for m in months[1:]:
            d, bd = max(m["days"], 1), max(base["days"], 1)
            at_base = 0.0
            for k, v in m["skus"].items():
                rate = base_rate.get(k) or (v["cost"] / v["dbus"] if v["dbus"] else 0.0)
                at_base += v["dbus"] / d * rate
            base_pd = base["cost"] / bd
            m["vs_first"] = {"cost_per_day_change": m["cost_per_day"] - base_pd,
                             "pct": ((m["cost_per_day"] / base_pd - 1) * 100) if base_pd else None,
                             "volume_effect_per_day": at_base - base_pd,
                             "price_effect_per_day": m["cost_per_day"] - at_base}
    tiers = [m["dominant_tier"] for m in months if m["dominant_tier"]]
    changed = len(set(tiers)) > 1
    return {"months": months, "tier_changed": changed,
            "tier_sequence": [(m["month"], m["dominant_tier"]) for m in months]}
