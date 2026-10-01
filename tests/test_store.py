from pathlib import Path

import pytest

from civicflow.postgres_store import MIGRATION_PATH, PostgresCaseStore
from civicflow.store import SQLiteCaseStore, build_case_store


def test_store_factory_preserves_sqlite_default(tmp_path: Path) -> None:
    database = tmp_path / "factory.db"

    store = build_case_store(database, database_url="")

    assert isinstance(store, SQLiteCaseStore)
    assert store.database == database


def test_store_factory_selects_postgresql_explicitly() -> None:
    store = build_case_store(database_url="postgresql://service:secret@db/civicflow")

    assert isinstance(store, PostgresCaseStore)
    assert store.backend_name == "postgresql"


def test_store_factory_rejects_ambiguous_database_urls() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        build_case_store(database_url="mysql://db/civicflow")


def test_postgres_migration_defines_governed_operational_schema() -> None:
    migration = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE dim_case" in migration
    assert "CREATE TABLE fact_case_status_event" in migration
    assert "CREATE TABLE ingestion_receipt" in migration
    assert "TIMESTAMPTZ" in migration
    assert "CREATE VIEW current_case_state" in migration
