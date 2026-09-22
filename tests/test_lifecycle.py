import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from civicflow.model import Status
from civicflow.synthetic import generate_cases, generate_status_events
from civicflow.validation import ValidationError, validate_status_events
from civicflow.warehouse import load_warehouse

AS_OF = datetime(2026, 9, 22, 12, tzinfo=UTC)


def test_generated_histories_reconcile_to_snapshots() -> None:
    cases = generate_cases(500, seed=51, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    validate_status_events(cases, events)
    assert {event.case_id for event in events} == {case.case_id for case in cases}
    assert all(event.occurred_at <= AS_OF for event in events)


def test_validation_rejects_discontinuous_transition() -> None:
    case = replace(generate_cases(1, as_of=AS_OF)[0], status=Status.IN_PROGRESS)
    events = generate_status_events([case], as_of=AS_OF)
    events[1] = replace(events[1], from_status=Status.RESOLVED)
    with pytest.raises(ValidationError, match="discontinuous transition"):
        validate_status_events([case], events)


def test_validation_rejects_orphan_event() -> None:
    cases = generate_cases(1, as_of=AS_OF)
    event = replace(generate_status_events(cases, as_of=AS_OF)[0], case_id="CF-UNKNOWN")
    with pytest.raises(ValidationError, match="unknown case_id"):
        validate_status_events(cases, [event])


def test_warehouse_reconciles_dimensions_facts_and_current_state(tmp_path) -> None:
    cases = generate_cases(100, seed=17, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    database = tmp_path / "civicflow.db"
    load_warehouse(database, cases, events)

    with sqlite3.connect(database) as connection:
        case_count = connection.execute("SELECT COUNT(*) FROM dim_case").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM fact_case_status_event").fetchone()[
            0
        ]
        current_count = connection.execute("SELECT COUNT(*) FROM current_case_state").fetchone()[0]
        mismatches = connection.execute(
            """SELECT COUNT(*) FROM current_case_state
            WHERE (closed_at IS NULL AND current_status IN ('resolved', 'closed'))
               OR (closed_at IS NOT NULL AND current_status NOT IN ('resolved', 'closed'))"""
        ).fetchone()[0]

    assert case_count == current_count == len(cases)
    assert event_count == len(events)
    assert mismatches == 0


def test_warehouse_refresh_is_idempotent(tmp_path) -> None:
    cases = generate_cases(10, seed=7, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    database = tmp_path / "civicflow.db"
    load_warehouse(database, cases, events)
    load_warehouse(database, cases, events)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dim_case").fetchone()[0] == 10


def test_terminal_event_uses_exact_closed_timestamp() -> None:
    case = generate_cases(1, as_of=AS_OF)[0]
    case = replace(case, status=Status.CLOSED, closed_at=case.opened_at + timedelta(hours=3))
    events = generate_status_events([case], as_of=AS_OF)
    assert events[-1].occurred_at == case.closed_at
