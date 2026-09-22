"""Auditable SLA and workload metrics for service operations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True, slots=True)
class BacklogMetric:
    department: str
    open_cases: int
    currently_breached: int
    due_within_24_hours: int
    average_age_hours: float
    maximum_age_hours: float
    age_0_24_hours: int
    age_25_72_hours: int
    age_73_168_hours: int
    age_over_168_hours: int


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


def build_backlog_report(cases: list[ServiceCase], *, as_of: datetime) -> list[BacklogMetric]:
    """Summarize active workload age and near-term SLA exposure by department."""
    validate_cases(cases)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    active: dict[str, list[tuple[ServiceCase, float]]] = defaultdict(list)
    for case in cases:
        if case.closed_at is not None:
            continue
        age = (as_of - case.opened_at).total_seconds() / 3600
        if age < 0:
            raise ValueError(f"as_of precedes opened_at for {case.case_id}")
        active[str(case.department)].append((case, age))

    report: list[BacklogMetric] = []
    for department, rows in sorted(active.items()):
        ages = [age for _, age in rows]
        report.append(
            BacklogMetric(
                department=department,
                open_cases=len(rows),
                currently_breached=sum(age > case.target_hours for case, age in rows),
                due_within_24_hours=sum(0 <= case.target_hours - age <= 24 for case, age in rows),
                average_age_hours=round(sum(ages) / len(ages), 2),
                maximum_age_hours=round(max(ages), 2),
                age_0_24_hours=sum(age <= 24 for age in ages),
                age_25_72_hours=sum(24 < age <= 72 for age in ages),
                age_73_168_hours=sum(72 < age <= 168 for age in ages),
                age_over_168_hours=sum(age > 168 for age in ages),
            )
        )
    return report
