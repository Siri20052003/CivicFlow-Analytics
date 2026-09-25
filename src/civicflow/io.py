"""Stable CSV and JSON serialization boundaries."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from civicflow.analytics import BacklogMetric, SlaMetric
from civicflow.geography import DistrictServiceMetric
from civicflow.model import ServiceCase
from civicflow.workflow import CohortMetric, CycleTimeMetric


def write_cases_csv(cases: list[ServiceCase], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = [case.to_record() for case in cases]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def write_report_json(
    metrics: list[SlaMetric]
    | list[BacklogMetric]
    | list[CycleTimeMetric]
    | list[CohortMetric]
    | list[DistrictServiceMetric],
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump([asdict(metric) for metric in metrics], handle, indent=2)
        handle.write("\n")


def write_dataclass_json(value: object, destination: Path) -> None:
    """Serialize a dataclass report with a stable trailing newline."""
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("value must be a dataclass instance")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(asdict(value), handle, indent=2)
        handle.write("\n")
