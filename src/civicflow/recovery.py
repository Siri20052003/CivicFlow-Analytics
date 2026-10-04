"""PostgreSQL backup restoration rehearsals with measurable recovery evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from civicflow.postgres_store import _psycopg

TABLE_KEYS = {
    "dim_case": "case_id",
    "fact_case_status_event": "event_id",
    "ingestion_receipt": "request_id",
}
REHEARSAL_DATABASE_PATTERN = re.compile(r"^civicflow_rehearsal_[a-z0-9_]{1,32}$")


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    maximum_recovery_seconds: float
    maximum_recovery_point_age_seconds: float
    required_migration_version: int

    def validate(self) -> None:
        if not 1 <= self.maximum_recovery_seconds <= 3600:
            raise ValueError("maximum_recovery_seconds must be between 1 and 3600")
        if not 1 <= self.maximum_recovery_point_age_seconds <= 86400:
            raise ValueError("maximum_recovery_point_age_seconds must be between 1 and 86400")
        if self.required_migration_version < 1:
            raise ValueError("required_migration_version must be positive")


@dataclass(frozen=True, slots=True)
class TableEvidence:
    rows: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RecoveryCheck:
    name: str
    actual: str | float | int
    expected: str | float | int
    status: str


@dataclass(frozen=True, slots=True)
class RecoveryRehearsalReport:
    schema_version: str
    status: str
    started_at: str
    completed_at: str
    recovery_point_at: str
    source_database: str
    rehearsal_database: str
    backup_file: str
    backup_bytes: int
    backup_sha256: str
    backup_seconds: float
    restore_seconds: float
    verification_seconds: float
    recovery_seconds: float
    recovery_point_age_seconds: float
    cleanup_status: str
    policy: RecoveryPolicy
    source_tables: dict[str, TableEvidence]
    restored_tables: dict[str, TableEvidence]
    checks: list[RecoveryCheck]


def load_recovery_policy(path: Path) -> RecoveryPolicy:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = {
        "maximum_recovery_seconds",
        "maximum_recovery_point_age_seconds",
        "required_migration_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"recovery policy must contain exactly {sorted(expected)}")
    policy = RecoveryPolicy(**payload)
    policy.validate()
    return policy


def validate_rehearsal_database_name(name: str) -> None:
    if not REHEARSAL_DATABASE_PATTERN.fullmatch(name):
        raise ValueError("rehearsal database must use the civicflow_rehearsal_ safety namespace")


def _database_name(database_url: str) -> str:
    psycopg = _psycopg()
    name = psycopg.conninfo.conninfo_to_dict(database_url).get("dbname")
    if not name:
        raise ValueError("database URL must declare a database name")
    return str(name)


def _database_url(database_url: str, database_name: str) -> str:
    psycopg = _psycopg()
    return str(psycopg.conninfo.make_conninfo(database_url, dbname=database_name))


def _postgres_environment(database_url: str) -> dict[str, str]:
    psycopg = _psycopg()
    values = psycopg.conninfo.conninfo_to_dict(database_url)
    mapping = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
    }
    environment = os.environ.copy()
    for source, target in mapping.items():
        if values.get(source) is not None:
            environment[target] = str(values[source])
    return environment


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required PostgreSQL client tool is unavailable: {name}")
    return path


def _run_tool(command: list[str], database_url: str, timeout_seconds: float) -> None:
    try:
        completed = subprocess.run(
            command,
            env=_postgres_environment(database_url),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"PostgreSQL tool exceeded {timeout_seconds:.0f}-second limit"
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "failed"
        raise RuntimeError(f"PostgreSQL tool failed: {detail}")


def _connection_evidence(connection: Any) -> tuple[int, dict[str, TableEvidence]]:
    evidence: dict[str, TableEvidence] = {}
    migration = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM civicflow_schema_migration"
    ).fetchone()[0]
    for table, key in TABLE_KEYS.items():
        rows = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        digest = hashlib.sha256()
        with connection.cursor().copy(
            f"COPY (SELECT * FROM {table} ORDER BY {key}) TO STDOUT WITH (FORMAT CSV)"
        ) as copy:
            for block in copy:
                digest.update(bytes(block))
        evidence[table] = TableEvidence(rows=rows, sha256=digest.hexdigest())
    orphans = connection.execute(
        """SELECT COUNT(*) FROM fact_case_status_event AS event
        LEFT JOIN dim_case AS service_case ON service_case.case_id = event.case_id
        WHERE service_case.case_id IS NULL"""
    ).fetchone()[0]
    if orphans:
        raise ValueError(f"database contains {orphans} orphan lifecycle events")
    return int(migration), evidence


def _table_evidence(database_url: str) -> tuple[int, dict[str, TableEvidence]]:
    psycopg = _psycopg()
    with psycopg.connect(database_url) as connection:
        return _connection_evidence(connection)


def _check(name: str, actual: Any, expected: Any) -> RecoveryCheck:
    return RecoveryCheck(
        name=name,
        actual=actual,
        expected=expected,
        status="pass" if actual == expected else "fail",
    )


def _objective_check(name: str, actual: float, maximum: float) -> RecoveryCheck:
    return RecoveryCheck(
        name=name,
        actual=round(actual, 3),
        expected=maximum,
        status="pass" if actual <= maximum else "fail",
    )


def run_recovery_rehearsal(
    database_url: str,
    backup_path: Path,
    policy: RecoveryPolicy,
    *,
    rehearsal_database: str,
    started_at: datetime | None = None,
) -> RecoveryRehearsalReport:
    """Back up, restore, verify, and remove one isolated PostgreSQL rehearsal database."""
    policy.validate()
    validate_rehearsal_database_name(rehearsal_database)
    source_database = _database_name(database_url)
    if source_database == rehearsal_database:
        raise ValueError("source and rehearsal databases must differ")
    if backup_path.exists():
        raise FileExistsError(f"backup path already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    started = started_at or datetime.now(UTC)
    if started.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    started = started.astimezone(UTC)
    psycopg = _psycopg()
    admin_url = _database_url(database_url, "postgres")
    restored_url = _database_url(database_url, rehearsal_database)
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}-", dir=backup_path.parent
    )
    os.close(temporary_descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    backup_started = perf_counter()
    try:
        with psycopg.connect(database_url) as source_connection:
            source_connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            recovery_point = source_connection.execute("SELECT clock_timestamp()").fetchone()[0]
            exported_snapshot = source_connection.execute("SELECT pg_export_snapshot()").fetchone()[
                0
            ]
            source_migration, source_tables = _connection_evidence(source_connection)
            _run_tool(
                [
                    _require_tool("pg_dump"),
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    f"--snapshot={exported_snapshot}",
                    f"--file={temporary}",
                ],
                database_url,
                policy.maximum_recovery_seconds,
            )
        temporary.replace(backup_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    backup_seconds = perf_counter() - backup_started
    backup_content = backup_path.read_bytes()

    created = False
    cleanup_status = "not_started"
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (rehearsal_database,)
            ).fetchone()
            if exists:
                raise ValueError("rehearsal database already exists; refusing to replace it")
            connection.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(
                    psycopg.sql.Identifier(rehearsal_database)
                )
            )
            created = True

        restore_started = perf_counter()
        _run_tool(
            [
                _require_tool("pg_restore"),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                f"--dbname={rehearsal_database}",
                str(backup_path),
            ],
            restored_url,
            policy.maximum_recovery_seconds,
        )
        restore_seconds = perf_counter() - restore_started
        verification_started = perf_counter()
        restored_migration, restored_tables = _table_evidence(restored_url)
        verification_seconds = perf_counter() - verification_started
        completed = datetime.now(UTC)
        recovery_seconds = restore_seconds + verification_seconds
        recovery_point_age_seconds = (completed - recovery_point).total_seconds()
        checks = [
            _check(
                "source_migration_version",
                source_migration,
                policy.required_migration_version,
            ),
            _check(
                "restored_migration_version",
                restored_migration,
                policy.required_migration_version,
            ),
            *[
                _check(f"{table}_row_count", restored_tables[table].rows, source.rows)
                for table, source in source_tables.items()
            ],
            *[
                _check(f"{table}_sha256", restored_tables[table].sha256, source.sha256)
                for table, source in source_tables.items()
            ],
            _objective_check(
                "maximum_recovery_seconds",
                recovery_seconds,
                policy.maximum_recovery_seconds,
            ),
            _objective_check(
                "maximum_recovery_point_age_seconds",
                recovery_point_age_seconds,
                policy.maximum_recovery_point_age_seconds,
            ),
        ]
        status = "pass" if all(check.status == "pass" for check in checks) else "fail"
    finally:
        if created:
            with psycopg.connect(admin_url, autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (rehearsal_database,),
                )
                connection.execute(
                    psycopg.sql.SQL("DROP DATABASE {}").format(
                        psycopg.sql.Identifier(rehearsal_database)
                    )
                )
            cleanup_status = "dropped"

    return RecoveryRehearsalReport(
        schema_version="1.0",
        status=status,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        recovery_point_at=recovery_point.isoformat(),
        source_database=source_database,
        rehearsal_database=rehearsal_database,
        backup_file=backup_path.name,
        backup_bytes=len(backup_content),
        backup_sha256=hashlib.sha256(backup_content).hexdigest(),
        backup_seconds=round(backup_seconds, 3),
        restore_seconds=round(restore_seconds, 3),
        verification_seconds=round(verification_seconds, 3),
        recovery_seconds=round(recovery_seconds, 3),
        recovery_point_age_seconds=round(recovery_point_age_seconds, 3),
        cleanup_status=cleanup_status,
        policy=policy,
        source_tables=source_tables,
        restored_tables=restored_tables,
        checks=checks,
    )


def write_recovery_report(report: RecoveryRehearsalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
