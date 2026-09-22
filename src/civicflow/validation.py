"""Domain-level validation with actionable error messages."""

from __future__ import annotations

from collections import defaultdict

from civicflow.model import CaseStatusEvent, ServiceCase, Status


class ValidationError(ValueError):
    """Raised when one or more service cases violate the data contract."""


def validate_cases(cases: list[ServiceCase]) -> None:
    errors: list[str] = []
    seen: set[str] = set()
    for case in cases:
        prefix = case.case_id or "<missing-id>"
        if case.case_id in seen:
            errors.append(f"{prefix}: duplicate case_id")
        seen.add(case.case_id)
        if case.closed_at and case.closed_at < case.opened_at:
            errors.append(f"{prefix}: closed_at precedes opened_at")
        if case.target_hours <= 0:
            errors.append(f"{prefix}: target_hours must be positive")
        if not 1 <= case.district <= 10:
            errors.append(f"{prefix}: district must be between 1 and 10")
        if case.satisfaction_score is not None and not 1 <= case.satisfaction_score <= 5:
            errors.append(f"{prefix}: satisfaction_score must be between 1 and 5")
        if case.closed_at is None and case.satisfaction_score is not None:
            errors.append(f"{prefix}: open cases cannot have a satisfaction score")
    if errors:
        sample = "; ".join(errors[:10])
        suffix = f"; and {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ValidationError(f"case validation failed: {sample}{suffix}")


def validate_status_events(cases: list[ServiceCase], events: list[CaseStatusEvent]) -> None:
    """Reject orphaned, discontinuous, or snapshot-inconsistent event histories."""
    case_by_id = {case.case_id: case for case in cases}
    grouped: dict[str, list[CaseStatusEvent]] = defaultdict(list)
    seen_events: set[str] = set()
    errors: list[str] = []
    for event in events:
        if event.event_id in seen_events:
            errors.append(f"{event.event_id}: duplicate event_id")
        seen_events.add(event.event_id)
        if event.case_id not in case_by_id:
            errors.append(f"{event.event_id}: unknown case_id {event.case_id}")
            continue
        grouped[event.case_id].append(event)

    for case in cases:
        history = sorted(grouped[case.case_id], key=lambda event: event.occurred_at)
        if not history:
            errors.append(f"{case.case_id}: missing status history")
            continue
        if history[0].from_status is not None or history[0].to_status != Status.OPEN:
            errors.append(f"{case.case_id}: history must begin with an open event")
        for previous, current in zip(history, history[1:], strict=False):
            if current.from_status != previous.to_status:
                errors.append(f"{case.case_id}: discontinuous transition at {current.event_id}")
            if current.occurred_at < previous.occurred_at:
                errors.append(f"{case.case_id}: events are not chronological")
        if history[-1].to_status != case.status:
            errors.append(f"{case.case_id}: history does not match snapshot status")
        if history[0].occurred_at != case.opened_at:
            errors.append(f"{case.case_id}: first event does not match opened_at")
        if case.closed_at and history[-1].occurred_at != case.closed_at:
            errors.append(f"{case.case_id}: terminal event does not match closed_at")

    if errors:
        sample = "; ".join(errors[:10])
        suffix = f"; and {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ValidationError(f"event validation failed: {sample}{suffix}")
