"""CLI for a controlled PostgreSQL backup and restoration rehearsal."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from civicflow.recovery import (
    load_recovery_policy,
    run_recovery_rehearsal,
    write_recovery_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up and restore CivicFlow into a disposable verification database"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("CIVICFLOW_DATABASE_URL"),
        help="PostgreSQL URL; defaults to CIVICFLOW_DATABASE_URL",
    )
    parser.add_argument("--backup-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).with_name("recovery_policy.json"),
    )
    parser.add_argument("--rehearsal-database")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or CIVICFLOW_DATABASE_URL is required")
    database_name = args.rehearsal_database or datetime.now(UTC).strftime(
        "civicflow_rehearsal_%Y%m%d_%H%M%S"
    )
    report = run_recovery_rehearsal(
        args.database_url,
        args.backup_path,
        load_recovery_policy(args.policy),
        rehearsal_database=database_name,
    )
    write_recovery_report(report, args.report_path)
    print(
        f"status={report.status} backup_bytes={report.backup_bytes} "
        f"recovery_seconds={report.recovery_seconds:.3f} "
        f"recovery_point_age_seconds={report.recovery_point_age_seconds:.3f} "
        f"cleanup={report.cleanup_status}"
    )
    if report.status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
