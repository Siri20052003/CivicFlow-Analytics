"""Command-line entry point for reproducible local and CI workflows."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from civicflow.analytics import build_sla_report
from civicflow.io import write_cases_csv, write_report_json
from civicflow.synthetic import generate_cases
from civicflow.validation import validate_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and analyze synthetic civic cases")
    parser.add_argument("--cases", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    as_of = datetime.now(UTC).replace(microsecond=0)
    cases = generate_cases(args.cases, seed=args.seed, as_of=as_of)
    validate_cases(cases)
    report = build_sla_report(cases)
    write_cases_csv(cases, args.output_dir / "service_cases.csv")
    write_report_json(report, args.output_dir / "sla_report.json")
    closed = sum(metric.closed_cases for metric in report)
    print(f"generated={len(cases)} closed={closed} departments={len(report)} seed={args.seed}")


if __name__ == "__main__":
    main()
