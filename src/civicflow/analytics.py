"""Auditable SLA and workload metrics for service operations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from civicflow.model import ServiceCase
from civicflow.validation import validate_cases


@dataclass(frozen=True, slots=True)
class SlaMetric:
    department: str
    total_cases: int
    closed_cases: int
    open_cases: int
    breached_cases: int
    compliance_rate: float | None
    average_resolution_hours: float | None


def build_sla_report(cases: list[ServiceCase]) -> list[SlaMetric]:
    """Aggregate outcome metrics by department after validating the input contract."""
    validate_cases(cases)
    grouped: dict[str, list[ServiceCase]] = defaultdict(list)
    for case in cases:
        grouped[str(case.department)].append(case)

    report: list[SlaMetric] = []
    for department, rows in sorted(grouped.items()):
        closed = [row for row in rows if row.closed_at is not None]
        elapsed = [row.elapsed_hours for row in closed if row.elapsed_hours is not None]
        breaches = sum(row.met_sla is False for row in closed)
        report.append(
            SlaMetric(
                department=department,
                total_cases=len(rows),
                closed_cases=len(closed),
                open_cases=len(rows) - len(closed),
                breached_cases=breaches,
                compliance_rate=round((len(closed) - breaches) / len(closed), 4)
                if closed
                else None,
                average_resolution_hours=(
                    round(sum(elapsed) / len(elapsed), 2) if elapsed else None
                ),
            )
        )
    return report
