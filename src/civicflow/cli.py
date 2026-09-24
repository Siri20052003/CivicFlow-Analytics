"""Command-line entry point for reproducible local and CI workflows."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from civicflow.analytics import build_backlog_report, build_sla_report
from civicflow.geography import build_district_service_report
from civicflow.io import write_cases_csv, write_report_json
from civicflow.synthetic import generate_cases, generate_status_events
from civicflow.validation import validate_cases, validate_status_events
from civicflow.warehouse import load_warehouse
from civicflow.workflow import build_cohort_report, build_cycle_time_report


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
    events = generate_status_events(cases, as_of=as_of)
    validate_status_events(cases, events)
    backlog = build_backlog_report(cases, as_of=as_of)
    cycle_time = build_cycle_time_report(cases, events)
    cohorts = build_cohort_report(cases)
    districts = build_district_service_report(cases)
    write_cases_csv(cases, args.output_dir / "service_cases.csv")
    write_report_json(report, args.output_dir / "sla_report.json")
    write_report_json(backlog, args.output_dir / "backlog_report.json")
    write_report_json(cycle_time, args.output_dir / "cycle_time_report.json")
    write_report_json(cohorts, args.output_dir / "cohort_report.json")
    write_report_json(districts, args.output_dir / "district_service_report.json")
    load_warehouse(args.output_dir / "civicflow.db", cases, events)
    closed = sum(metric.closed_cases for metric in report)
    print(
        f"generated={len(cases)} events={len(events)} closed={closed} "
        f"departments={len(report)} seed={args.seed}"
    )


if __name__ == "__main__":
    main()
