"""Deterministic, realistic synthetic municipal service-case generator."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from civicflow.model import SLA_HOURS, Department, Priority, ServiceCase, Status

SERVICE_CATALOG: dict[Department, tuple[str, ...]] = {
    Department.PUBLIC_WORKS: ("pothole", "street_light", "illegal_dumping"),
    Department.CODE_COMPLIANCE: ("overgrown_lot", "unsafe_structure", "noise"),
    Department.PARKS: ("playground_repair", "trail_damage", "tree_hazard"),
    Department.WATER: ("water_leak", "low_pressure", "sewer_backup"),
    Department.TRANSPORTATION: ("signal_fault", "sign_damage", "blocked_sidewalk"),
}

TEAM_BY_DEPARTMENT = {
    Department.PUBLIC_WORKS: ("PW-North", "PW-South"),
    Department.CODE_COMPLIANCE: ("Code-East", "Code-West"),
    Department.PARKS: ("Parks-Field", "Urban-Forestry"),
    Department.WATER: ("Water-Rapid", "Water-Network"),
    Department.TRANSPORTATION: ("Traffic-Ops", "Right-of-Way"),
}


def generate_cases(
    count: int,
    *,
    seed: int = 20260922,
    as_of: datetime | None = None,
) -> list[ServiceCase]:
    """Generate reproducible cases while preserving realistic field relationships."""
    if count < 1:
        raise ValueError("count must be at least 1")
    as_of = as_of or datetime.now(UTC).replace(microsecond=0)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    rng = random.Random(seed)
    departments = list(Department)
    priorities = list(Priority)
    cases: list[ServiceCase] = []

    for index in range(1, count + 1):
        department = rng.choice(departments)
        priority = rng.choices(priorities, weights=(3, 17, 50, 30), k=1)[0]
        target = SLA_HOURS[priority]
        opened = as_of - timedelta(hours=rng.uniform(2, 24 * 120))
        is_closed = rng.random() < 0.82
        # High-pressure cases are usually resolved faster; a long tail creates credible breaches.
        duration_factor = rng.lognormvariate(
            -0.18 if priority == Priority.CRITICAL else -0.05, 0.55
        )
        elapsed = target * duration_factor
        closed = min(opened + timedelta(hours=elapsed), as_of) if is_closed else None
        if closed == as_of and opened + timedelta(hours=elapsed) > as_of:
            closed = None

        status = (
            rng.choice((Status.RESOLVED, Status.CLOSED))
            if closed
            else rng.choice((Status.OPEN, Status.IN_PROGRESS))
        )
        satisfaction = (
            max(1, min(5, round(rng.gauss(4.35 if elapsed <= target else 2.85, 0.75))))
            if closed
            else None
        )
        cases.append(
            ServiceCase(
                case_id=f"CF-{as_of.year}-{index:07d}",
                opened_at=opened,
                department=department,
                service_type=rng.choice(SERVICE_CATALOG[department]),
                priority=priority,
                channel=rng.choices(
                    ("web", "mobile", "phone", "field"), weights=(42, 28, 22, 8), k=1
                )[0],
                district=rng.randint(1, 10),
                status=status,
                assigned_team=rng.choice(TEAM_BY_DEPARTMENT[department]),
                target_hours=target,
                closed_at=closed,
                satisfaction_score=satisfaction,
            )
        )
    return cases
