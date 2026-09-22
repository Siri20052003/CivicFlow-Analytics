"""Transactional SQLite warehouse for case snapshots and immutable lifecycle facts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from civicflow.model import CaseStatusEvent, ServiceCase
from civicflow.validation import validate_cases, validate_status_events

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS dim_case (
    case_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    department TEXT NOT NULL,
    service_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    channel TEXT NOT NULL,
    district INTEGER NOT NULL CHECK (district BETWEEN 1 AND 10),
    assigned_team TEXT NOT NULL,
    target_hours INTEGER NOT NULL CHECK (target_hours > 0),
    closed_at TEXT,
    satisfaction_score INTEGER CHECK (satisfaction_score BETWEEN 1 AND 5)
);
CREATE TABLE IF NOT EXISTS fact_case_status_event (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES dim_case(case_id),
    occurred_at TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    assigned_team TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_case_time
    ON fact_case_status_event(case_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_case_department_open
    ON dim_case(department, closed_at);
CREATE VIEW IF NOT EXISTS current_case_state AS
SELECT c.*, e.to_status AS current_status, e.occurred_at AS status_changed_at
FROM dim_case AS c
JOIN fact_case_status_event AS e ON e.event_id = (
    SELECT latest.event_id FROM fact_case_status_event AS latest
    WHERE latest.case_id = c.case_id
    ORDER BY latest.occurred_at DESC, latest.event_id DESC LIMIT 1
);
"""


def load_warehouse(database: Path, cases: list[ServiceCase], events: list[CaseStatusEvent]) -> None:
    """Atomically replace a generated dataset after validating both contracts."""
    validate_cases(cases)
    validate_status_events(cases, events)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        with connection:
            connection.execute("DELETE FROM fact_case_status_event")
            connection.execute("DELETE FROM dim_case")
            connection.executemany(
                """INSERT INTO dim_case VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        case.case_id,
                        case.opened_at.isoformat(),
                        str(case.department),
                        case.service_type,
                        str(case.priority),
                        case.channel,
                        case.district,
                        case.assigned_team,
                        case.target_hours,
                        case.closed_at.isoformat() if case.closed_at else None,
                        case.satisfaction_score,
                    )
                    for case in cases
                ],
            )
            connection.executemany(
                """INSERT INTO fact_case_status_event VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        event.event_id,
                        event.case_id,
                        event.occurred_at.isoformat(),
                        str(event.from_status) if event.from_status else None,
                        str(event.to_status),
                        event.assigned_team,
                    )
                    for event in events
                ],
            )
