from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from civicflow.postgres_store import MIGRATION_VERSION, PostgresCaseStore
from civicflow.synthetic import generate_cases, generate_status_events
from civicflow.warehouse import IngestionConflict

DATABASE_URL = os.environ.get("CIVICFLOW_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="PostgreSQL service is not configured")
AS_OF = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def store() -> PostgresCaseStore:
    import psycopg

    assert DATABASE_URL is not None
    case_store = PostgresCaseStore(DATABASE_URL)
    case_store.initialize()
    case_store.initialize()
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("TRUNCATE ingestion_receipt, fact_case_status_event, dim_case CASCADE")
    return case_store


def test_migration_is_versioned_and_readiness_checks_schema(store: PostgresCaseStore) -> None:
    import psycopg

    assert DATABASE_URL is not None
    ready, detail = store.readiness()
    with psycopg.connect(DATABASE_URL) as connection:
        versions = connection.execute(
            "SELECT version FROM civicflow_schema_migration ORDER BY version"
        ).fetchall()

    assert (ready, detail) == (True, "ready")
    assert versions == [(MIGRATION_VERSION,)]


def test_concurrent_replicas_commit_once_and_replay_safely(
    store: PostgresCaseStore,
) -> None:
    cases = generate_cases(1, seed=101, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(
                lambda _: store.ingest("concurrent-request", cases, events),
                range(12),
            )
        )

    total, rows = store.list_cases(limit=10, offset=0)
    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == 11
    assert total == 1
    assert rows[0]["case_id"] == cases[0].case_id


def test_changed_payload_reusing_key_is_rejected(store: PostgresCaseStore) -> None:
    cases = generate_cases(1, seed=102, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    store.ingest("immutable-request", cases, events)
    changed = [replace(cases[0], service_type=f"{cases[0].service_type} follow-up")]

    with pytest.raises(IngestionConflict, match="different payload"):
        store.ingest("immutable-request", changed, events)
