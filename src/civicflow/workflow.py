"""Cycle-time and cohort analytics derived from the immutable case event ledger."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import ceil

from civicflow.model import CaseStatusEvent, ServiceCase, Status
from civicflow.validation import validate_cases, validate_status_events


@dataclass(frozen=True, slots=True)
class CycleTimeMetric:
    department: str
    completed_cases: int
    median_first_action_hours: float
    p90_first_action_hours: float
    median_active_work_hours: float
    p90_active_work_hours: float
    median_resolution_hours: float
    p90_resolution_hours: float


@dataclass(frozen=True, slots=True)
class CohortMetric:
    opened_month: str
    department: str
    total_cases: int
    closed_cases: int
    breached_cases: int
    compliance_rate: float | None
    median_resolution_hours: float | None


def _percentile(values: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile, suitable for operational SLA reporting."""
    if not values:
        raise ValueError("percentile requires at least one value")
    rank = max(1, ceil(percentile * len(values)))
    return round(sorted(values)[rank - 1], 2)


def build_cycle_time_report(
    cases: list[ServiceCase], events: list[CaseStatusEvent]
) -> list[CycleTimeMetric]:
    """Measure handoff, active-work, and end-to-end cycle time by department."""
    validate_cases(cases)
    validate_status_events(cases, events)
    case_by_id = {case.case_id: case for case in cases}
    histories: dict[str, dict[Status, datetime]] = defaultdict(dict)
    for event in events:
        histories[event.case_id][event.to_status] = event.occurred_at

    grouped: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for case_id, timestamps in histories.items():
        case = case_by_id[case_id]
        opened = timestamps.get(Status.OPEN)
        started = timestamps.get(Status.IN_PROGRESS)
        resolved = timestamps.get(Status.RESOLVED)
        if not (opened and started and resolved):
            continue
        first_action = (started - opened).total_seconds() / 3600
        active_work = (resolved - started).total_seconds() / 3600
        resolution = (resolved - opened).total_seconds() / 3600
        grouped[str(case.department)].append((first_action, active_work, resolution))

    report: list[CycleTimeMetric] = []
    for department, rows in sorted(grouped.items()):
        first_action = [row[0] for row in rows]
        active_work = [row[1] for row in rows]
        resolution = [row[2] for row in rows]
        report.append(
            CycleTimeMetric(
                department=department,
                completed_cases=len(rows),
                median_first_action_hours=_percentile(first_action, 0.5),
                p90_first_action_hours=_percentile(first_action, 0.9),
                median_active_work_hours=_percentile(active_work, 0.5),
                p90_active_work_hours=_percentile(active_work, 0.9),
                median_resolution_hours=_percentile(resolution, 0.5),
                p90_resolution_hours=_percentile(resolution, 0.9),
            )
        )
    return report


def build_cohort_report(cases: list[ServiceCase]) -> list[CohortMetric]:
    """Group outcome metrics by intake month and department without survivor bias."""
    validate_cases(cases)
    grouped: dict[tuple[str, str], list[ServiceCase]] = defaultdict(list)
    for case in cases:
        month = case.opened_at.strftime("%Y-%m")
        grouped[(month, str(case.department))].append(case)

    report: list[CohortMetric] = []
    for (month, department), rows in sorted(grouped.items()):
        closed = [case for case in rows if case.elapsed_hours is not None]
        elapsed = [case.elapsed_hours for case in closed if case.elapsed_hours is not None]
        breaches = sum(case.met_sla is False for case in closed)
        report.append(
            CohortMetric(
                opened_month=month,
                department=department,
                total_cases=len(rows),
                closed_cases=len(closed),
                breached_cases=breaches,
                compliance_rate=round((len(closed) - breaches) / len(closed), 4)
                if closed
                else None,
                median_resolution_hours=_percentile(elapsed, 0.5) if elapsed else None,
            )
        )
    return report
