"""Multi-replica PostgreSQL implementation of the CivicFlow case store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from civicflow.model import CaseStatusEvent, ServiceCase
from civicflow.validation import validate_cases, validate_status_events
from civicflow.warehouse import (
    IngestionConflict,
    IngestionResult,
    case_row,
    event_row,
    payload_fingerprint,
)

MIGRATION_VERSION = 1
MIGRATION_PATH = Path(__file__).parent / "migrations" / "postgres_001_initial.sql"


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "PostgreSQL support requires: pip install 'civicflow-analytics[postgres]'"
        ) from error
    return psycopg


class PostgresCaseStore:
    """Transactional store safe for concurrent API replicas."""

    backend_name = "postgresql"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def initialize(self) -> None:
        psycopg = _psycopg()
        migration = MIGRATION_PATH.read_text(encoding="utf-8")
        with psycopg.connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(11286792511901)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS civicflow_schema_migration (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
                )"""
            )
            applied = connection.execute(
                "SELECT 1 FROM civicflow_schema_migration WHERE version = %s",
                (MIGRATION_VERSION,),
            ).fetchone()
            if applied is None:
                connection.execute(migration)
                connection.execute(
                    "INSERT INTO civicflow_schema_migration VALUES (%s, %s)",
                    (MIGRATION_VERSION, datetime.now(UTC)),
                )

    def readiness(self) -> tuple[bool, str]:
        psycopg = _psycopg()
        try:
            with psycopg.connect(self.database_url, connect_timeout=2) as connection:
                row = connection.execute(
                    """SELECT
                    to_regclass('public.dim_case'),
                    to_regclass('public.fact_case_status_event'),
                    to_regclass('public.ingestion_receipt'),
                    EXISTS (
                        SELECT 1 FROM civicflow_schema_migration WHERE version = %s
                    )""",
                    (MIGRATION_VERSION,),
                ).fetchone()
        except psycopg.Error as error:
            return False, str(error)
        if row is None or any(value is None for value in row[:3]) or not row[3]:
            return False, "required migration is not applied"
        return True, "ready"

    def ingest(
        self,
        request_id: str,
        cases: list[ServiceCase],
        events: list[CaseStatusEvent],
    ) -> IngestionResult:
        validate_cases(cases)
        validate_status_events(cases, events)
        fingerprint = payload_fingerprint(cases, events)
        psycopg = _psycopg()
        try:
            with psycopg.connect(self.database_url) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (request_id,),
                )
                receipt = connection.execute(
                    """SELECT payload_sha256, accepted_cases, accepted_events
                    FROM ingestion_receipt WHERE request_id = %s""",
                    (request_id,),
                ).fetchone()
                if receipt:
                    if receipt[0] != fingerprint:
                        message = (
                            f"request_id {request_id!r} was already used for a different payload"
                        )
                        raise IngestionConflict(message)
                    return IngestionResult(request_id, receipt[1], receipt[2], replayed=True)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO dim_case VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        [case_row(case) for case in cases],
                    )
                    cursor.executemany(
                        """INSERT INTO fact_case_status_event VALUES
                        (%s, %s, %s, %s, %s, %s)""",
                        [event_row(event) for event in events],
                    )
                connection.execute(
                    "INSERT INTO ingestion_receipt VALUES (%s, %s, %s, %s, %s)",
                    (
                        request_id,
                        fingerprint,
                        len(cases),
                        len(events),
                        datetime.now(UTC),
                    ),
                )
        except IngestionConflict:
            raise
        except psycopg.IntegrityError as error:
            raise IngestionConflict(f"batch conflicts with stored entity IDs: {error}") from error
        return IngestionResult(request_id, len(cases), len(events), replayed=False)

    def list_cases(self, limit: int, offset: int) -> tuple[int, list[dict[str, object]]]:
        psycopg = _psycopg()
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS count FROM current_case_state"
            ).fetchone()
            rows = connection.execute(
                """SELECT * FROM current_case_state
                ORDER BY opened_at DESC, case_id LIMIT %s OFFSET %s""",
                (limit, offset),
            ).fetchall()
        return int(total_row["count"]), list(rows)
