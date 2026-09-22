from dataclasses import replace
from datetime import UTC, datetime

import pytest

from civicflow.analytics import build_sla_report
from civicflow.synthetic import generate_cases
from civicflow.validation import ValidationError, validate_cases

AS_OF = datetime(2026, 9, 22, 12, tzinfo=UTC)


def test_sla_report_reconciles_to_source() -> None:
    cases = generate_cases(1_000, seed=42, as_of=AS_OF)
    report = build_sla_report(cases)
    assert sum(metric.total_cases for metric in report) == 1_000
    assert all(metric.total_cases == metric.closed_cases + metric.open_cases for metric in report)
    assert all(
        metric.compliance_rate is None or 0 <= metric.compliance_rate <= 1 for metric in report
    )


def test_validation_rejects_duplicate_ids() -> None:
    original = generate_cases(2, as_of=AS_OF)
    duplicate = replace(original[1], case_id=original[0].case_id)
    with pytest.raises(ValidationError, match="duplicate case_id"):
        validate_cases([original[0], duplicate])


def test_validation_rejects_impossible_closure() -> None:
    case = generate_cases(1, as_of=AS_OF)[0]
    invalid = replace(case, closed_at=case.opened_at.replace(year=2020))
    with pytest.raises(ValidationError, match="precedes opened_at"):
        validate_cases([invalid])
