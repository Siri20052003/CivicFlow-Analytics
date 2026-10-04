from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from civicflow.postgres_store import MIGRATION_VERSION, PostgresCaseStore
from civicflow.recovery import RecoveryPolicy, run_recovery_rehearsal
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


@pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="PostgreSQL client tools are not configured",
)
def test_backup_restoration_rehearsal_reconciles_and_cleans_up(
    store: PostgresCaseStore, tmp_path
) -> None:
    import psycopg

    assert DATABASE_URL is not None
    cases = generate_cases(7, seed=103, as_of=AS_OF)
    events = generate_status_events(cases, as_of=AS_OF)
    store.ingest("recovery-rehearsal", cases, events)
    report = run_recovery_rehearsal(
        DATABASE_URL,
        tmp_path / "civicflow.dump",
        RecoveryPolicy(120, 300, MIGRATION_VERSION),
        rehearsal_database="civicflow_rehearsal_integration",
        started_at=AS_OF,
    )

    assert report.status == "pass"
    assert report.cleanup_status == "dropped"
    assert report.backup_bytes > 0
    assert report.source_tables == report.restored_tables
    assert report.source_tables["dim_case"].rows == 7
    assert report.source_tables["fact_case_status_event"].rows == len(events)
    with psycopg.connect(DATABASE_URL) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (report.rehearsal_database,),
        ).fetchone()
    assert exists is None
