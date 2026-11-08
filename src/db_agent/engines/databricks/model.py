from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("high", "medium", "low", "info")


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str
    items: list[dict] = field(default_factory=list)
    fix: str = ""


@dataclass
class Report:
    meta: dict
    findings: list[Finding] = field(default_factory=list)
    cost: dict | None = None
    not_collected: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            c[f.severity] += 1
        return c
