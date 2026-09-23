from dataclasses import replace
from datetime import UTC, datetime

from civicflow.model import Status
from civicflow.synthetic import generate_cases, generate_status_events
from civicflow.workflow import build_cohort_report, build_cycle_time_report

AS_OF = datetime(2026, 9, 23, 12, tzinfo=UTC)


def test_cycle_time_report_reconciles_completed_histories() -> None:
    cases = generate_cases(600, seed=63, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    report = build_cycle_time_report(cases, events)
    expected = sum(case.status in (Status.RESOLVED, Status.CLOSED) for case in cases)
    assert sum(metric.completed_cases for metric in report) == expected
    assert all(
        metric.p90_first_action_hours >= metric.median_first_action_hours for metric in report
    )
    assert all(metric.p90_resolution_hours >= metric.median_resolution_hours for metric in report)


def test_cycle_time_uses_resolved_event_not_administrative_closure() -> None:
    case = generate_cases(1, seed=8, as_of=AS_OF)[0]
    case = replace(case, status=Status.CLOSED)
    events = generate_status_events([case], as_of=AS_OF)
    report = build_cycle_time_report([case], events)
    resolved_at = next(event.occurred_at for event in events if event.to_status == Status.RESOLVED)
    expected = round((resolved_at - case.opened_at).total_seconds() / 3600, 2)
    assert report[0].median_resolution_hours == expected


def test_cohorts_reconcile_cases_and_closed_outcomes() -> None:
    cases = generate_cases(1_000, seed=12, as_of=AS_OF)
    report = build_cohort_report(cases)
    assert sum(metric.total_cases for metric in report) == len(cases)
    assert sum(metric.closed_cases for metric in report) == sum(
        case.closed_at is not None for case in cases
    )
    assert all(
        metric.compliance_rate is None or 0 <= metric.compliance_rate <= 1 for metric in report
    )
