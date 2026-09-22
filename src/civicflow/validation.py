"""Domain-level validation with actionable error messages."""

from __future__ import annotations

from civicflow.model import ServiceCase


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
