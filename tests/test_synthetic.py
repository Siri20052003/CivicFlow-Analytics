from datetime import UTC, datetime

import pytest

from civicflow.model import SLA_HOURS
from civicflow.synthetic import generate_cases
from civicflow.validation import validate_cases

AS_OF = datetime(2026, 9, 22, 12, tzinfo=UTC)


def test_generation_is_deterministic() -> None:
    first = generate_cases(25, seed=7, as_of=AS_OF)
    second = generate_cases(25, seed=7, as_of=AS_OF)
    assert first == second


def test_generated_cases_follow_domain_contract() -> None:
    cases = generate_cases(2_000, seed=11, as_of=AS_OF)
    validate_cases(cases)
    assert len({case.case_id for case in cases}) == 2_000
    assert all(case.target_hours == SLA_HOURS[case.priority] for case in cases)
    assert all(case.closed_at is None or case.closed_at <= AS_OF for case in cases)


def test_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        generate_cases(0, as_of=AS_OF)


def test_as_of_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_cases(1, as_of=datetime(2026, 9, 22))
