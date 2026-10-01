"""Backend-neutral operational case-store boundary."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Protocol

from civicflow.model import CaseStatusEvent, ServiceCase
from civicflow.warehouse import IngestionResult, ingest_case_batch, initialize_warehouse


class CaseStore(Protocol):
    """Persistence operations required by the real-time API."""

    backend_name: str

    def initialize(self) -> None: ...

    def readiness(self) -> tuple[bool, str]: ...

    def ingest(
        self,
        request_id: str,
        cases: list[ServiceCase],
        events: list[CaseStatusEvent],
    ) -> IngestionResult: ...

    def list_cases(self, limit: int, offset: int) -> tuple[int, list[dict[str, object]]]: ...


class SQLiteCaseStore:
    """Single-replica SQLite implementation used for local demonstrations."""

    backend_name = "sqlite"

    def __init__(self, database: Path) -> None:
        self.database = database

    def initialize(self) -> None:
        initialize_warehouse(self.database)

    def readiness(self) -> tuple[bool, str]:
        try:
            with sqlite3.connect(
                f"file:{self.database}?mode=rw", uri=True, timeout=2
            ) as connection:
                connection.execute("SELECT 1").fetchone()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
        except sqlite3.Error as error:
            return False, str(error)
        required = {"dim_case", "fact_case_status_event", "ingestion_receipt"}
        missing = sorted(required - tables)
        return (False, f"missing tables: {', '.join(missing)}") if missing else (True, "ready")

    def ingest(
        self,
        request_id: str,
        cases: list[ServiceCase],
        events: list[CaseStatusEvent],
    ) -> IngestionResult:
        return ingest_case_batch(self.database, request_id, cases, events)

    def list_cases(self, limit: int, offset: int) -> tuple[int, list[dict[str, object]]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute("SELECT COUNT(*) FROM current_case_state").fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM current_case_state
                ORDER BY opened_at DESC, case_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return total, [dict(row) for row in rows]


def build_case_store(
    database: Path | None = None,
    *,
    database_url: str | None = None,
) -> CaseStore:
    """Select PostgreSQL explicitly, otherwise preserve the local SQLite default."""
    configured_url = (
        database_url if database_url is not None else os.environ.get("CIVICFLOW_DATABASE_URL", "")
    )
    if configured_url:
        if not configured_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("CIVICFLOW_DATABASE_URL must use postgresql:// or postgres://")
        from civicflow.postgres_store import PostgresCaseStore

        return PostgresCaseStore(configured_url)
    sqlite_path = database or Path(os.environ.get("CIVICFLOW_DB", "data/generated/civicflow.db"))
    return SQLiteCaseStore(sqlite_path)
