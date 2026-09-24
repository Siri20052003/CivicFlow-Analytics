from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from civicflow.geography import (
    DISTRICT_PROFILES,
    build_district_service_report,
    district_metric_from_counts,
    wilson_interval,
)
from civicflow.model import Status
from civicflow.synthetic import generate_cases

AS_OF = datetime(2026, 9, 24, 12, tzinfo=UTC)


def test_district_report_reconciles_source_and_metadata() -> None:
    cases = generate_cases(5_000, seed=24, as_of=AS_OF)
    report = build_district_service_report(cases)
    assert len(report) == len(DISTRICT_PROFILES) == 10
    assert sum(metric.total_cases for metric in report) == len(cases)
    assert sum(metric.closed_cases for metric in report) == sum(
        case.closed_at is not None for case in cases
    )
    assert all(metric.population > 0 for metric in report)
    for metric in report:
        assert metric.confidence_lower is not None
        assert metric.compliance_rate is not None
        assert metric.confidence_upper is not None
        assert metric.confidence_lower <= metric.compliance_rate <= metric.confidence_upper


def test_small_cohort_suppresses_outcome_rate() -> None:
    metric = district_metric_from_counts(
        1,
        total_cases=31,
        closed_cases=29,
        compliant_cases=28,
        citywide_compliance_rate=0.8,
    )
    assert metric.comparison == "suppressed"
    assert metric.compliance_rate is None
    assert metric.confidence_lower is None
    assert metric.gap_percentage_points is None
    assert metric.suppression_reason == "fewer than 30 closed cases"


def test_confidence_interval_controls_benchmark_flag() -> None:
    lower = district_metric_from_counts(
        3,
        total_cases=120,
        closed_cases=100,
        compliant_cases=45,
        citywide_compliance_rate=0.8,
    )
    uncertain = district_metric_from_counts(
        4,
        total_cases=45,
        closed_cases=30,
        compliant_cases=23,
        citywide_compliance_rate=0.8,
    )
    assert lower.comparison == "statistically_lower"
    assert uncertain.comparison == "no_clear_difference"


def test_wilson_interval_validates_counts() -> None:
    assert wilson_interval(80, 100) == (0.7112, 0.8666)
    with pytest.raises(ValueError, match="between zero and total"):
        wilson_interval(11, 10)


def test_report_suppresses_an_artificially_small_district() -> None:
    cases = generate_cases(40, seed=2, as_of=AS_OF)
    district_one = [replace(case, district=1) for case in cases[:10]]
    district_two = [
        replace(
            case,
            case_id=f"LARGE-{index}",
            district=2,
            status=Status.CLOSED,
            closed_at=case.opened_at + timedelta(hours=1),
            satisfaction_score=5,
        )
        for index, case in enumerate(cases[10:], start=1)
    ]
    report = build_district_service_report(district_one + district_two)
    assert report[0].comparison == "suppressed"
    assert report[1].compliance_rate == 1.0
