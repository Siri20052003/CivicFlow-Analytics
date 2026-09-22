"""Stable CSV and JSON serialization boundaries."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from civicflow.analytics import SlaMetric
from civicflow.model import ServiceCase


def write_cases_csv(cases: list[ServiceCase], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = [case.to_record() for case in cases]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def write_report_json(metrics: list[SlaMetric], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump([asdict(metric) for metric in metrics], handle, indent=2)
        handle.write("\n")
