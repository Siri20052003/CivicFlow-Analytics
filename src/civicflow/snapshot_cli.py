"""Command-line workflow for governed executive briefing snapshots."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from civicflow.snapshots import (
    RETENTION_REPORT_NAME,
    create_snapshot,
    enforce_retention,
    load_retention_policy,
    write_retention_report,
)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be valid ISO 8601") from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and retain integrity-verified CivicFlow briefing snapshots"
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).with_name("snapshot_retention.json"),
    )
    parser.add_argument("--as-of", type=_timestamp)
    parser.add_argument(
        "--apply-retention",
        action="store_true",
        help="delete eligible verified snapshots; omission is always a dry run",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    as_of = args.as_of or datetime.now(UTC)
    snapshot = create_snapshot(args.source_dir, args.snapshot_dir, created_at=as_of)
    report = enforce_retention(
        args.snapshot_dir,
        load_retention_policy(args.policy),
        evaluated_at=as_of,
        apply=args.apply_retention,
    )
    report_path = args.snapshot_dir / RETENTION_REPORT_NAME
    write_retention_report(report, report_path)
    print(
        f"snapshot={snapshot.name} mode={report.mode} "
        f"eligible={sum(item.decision == 'delete' for item in report.decisions)} "
        f"deleted={len(report.deleted_snapshot_ids)}"
    )


if __name__ == "__main__":
    main()
