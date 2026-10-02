"""Governance controls for staffing forecasts and planning assumptions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from civicflow.staffing import (
    DepartmentStaffingAssumption,
    StaffingBacktestReport,
)


@dataclass(frozen=True, slots=True)
class ForecastMonitoringThresholds:
    maximum_wape: float
    maximum_absolute_bias: float
    minimum_planning_coverage: float
    minimum_validation_weeks: int

    def validate(self) -> None:
        if not 0 < self.maximum_wape <= 1:
            raise ValueError("maximum_wape must be between 0 and 1")
        if self.maximum_absolute_bias <= 0:
            raise ValueError("maximum_absolute_bias must be positive")
        if not 0 < self.minimum_planning_coverage <= 1:
            raise ValueError("minimum_planning_coverage must be between 0 and 1")
        if self.minimum_validation_weeks < 4:
            raise ValueError("minimum_validation_weeks must be at least 4")


@dataclass(frozen=True, slots=True)
class ForecastMonitoringCheck:
    metric: str
    actual: float
    threshold: float
    comparison: str
    status: str


@dataclass(frozen=True, slots=True)
class DepartmentForecastMonitoring:
    department: str
    status: str
    checks: list[ForecastMonitoringCheck]


@dataclass(frozen=True, slots=True)
class ForecastMonitoringReport:
    status: str
    validation_start: str
    validation_end: str
    checks: list[ForecastMonitoringCheck]
    departments: list[DepartmentForecastMonitoring]


@dataclass(frozen=True, slots=True)
class AssumptionAuditReport:
    status: str
    versions: int
    latest_version: int
    latest_effective_at: str
    latest_entry_hash: str
    current_assumptions_match: bool
    changed_fields_in_latest_version: int


def load_monitoring_thresholds(path: Path) -> ForecastMonitoringThresholds:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = {
        "maximum_wape",
        "maximum_absolute_bias",
        "minimum_planning_coverage",
        "minimum_validation_weeks",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"monitoring thresholds must contain exactly {sorted(expected)}")
    thresholds = ForecastMonitoringThresholds(**payload)
    thresholds.validate()
    return thresholds


def _check(
    metric: str, actual: float, threshold: float, comparison: str
) -> ForecastMonitoringCheck:
    passed = actual <= threshold if comparison == "at_most" else actual >= threshold
    return ForecastMonitoringCheck(
        metric=metric,
        actual=actual,
        threshold=threshold,
        comparison=comparison,
        status="pass" if passed else "action_required",
    )


def _accuracy_checks(
    wape: float,
    bias: float,
    coverage: float,
    validation_weeks: int,
    thresholds: ForecastMonitoringThresholds,
) -> list[ForecastMonitoringCheck]:
    return [
        _check("weighted_absolute_percentage_error", wape, thresholds.maximum_wape, "at_most"),
        _check("absolute_mean_bias", abs(bias), thresholds.maximum_absolute_bias, "at_most"),
        _check(
            "planning_coverage_rate",
            coverage,
            thresholds.minimum_planning_coverage,
            "at_least",
        ),
        _check(
            "validation_weeks",
            float(validation_weeks),
            float(thresholds.minimum_validation_weeks),
            "at_least",
        ),
    ]


def evaluate_forecast_monitoring(
    backtest: StaffingBacktestReport,
    thresholds: ForecastMonitoringThresholds,
) -> ForecastMonitoringReport:
    """Evaluate overall and department accuracy against reviewable policy thresholds."""
    thresholds.validate()
    overall = _accuracy_checks(
        backtest.weighted_absolute_percentage_error,
        backtest.mean_bias,
        backtest.planning_coverage_rate,
        backtest.validation_weeks,
        thresholds,
    )
    departments: list[DepartmentForecastMonitoring] = []
    for department in backtest.departments:
        checks = _accuracy_checks(
            department.weighted_absolute_percentage_error,
            department.mean_bias,
            department.planning_coverage_rate,
            department.validation_weeks,
            thresholds,
        )
        departments.append(
            DepartmentForecastMonitoring(
                department=department.department,
                status=_status(checks),
                checks=checks,
            )
        )
    combined = overall + [check for item in departments for check in item.checks]
    return ForecastMonitoringReport(
        status=_status(combined),
        validation_start=backtest.validation_start,
        validation_end=backtest.validation_end,
        checks=overall,
        departments=departments,
    )


def _status(checks: list[ForecastMonitoringCheck]) -> str:
    return "pass" if all(check.status == "pass" for check in checks) else "action_required"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry_hash(entry: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest()


def _assumption_payload(
    assumptions: dict[str, DepartmentStaffingAssumption],
) -> dict[str, dict[str, object]]:
    return {department: asdict(value) for department, value in sorted(assumptions.items())}


def _validate_assumptions(payload: object, version: int) -> dict[str, DepartmentStaffingAssumption]:
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"audit version {version}: assumptions must be a non-empty object")
    parsed: dict[str, DepartmentStaffingAssumption] = {}
    for department, values in payload.items():
        if not isinstance(department, str) or not isinstance(values, dict):
            raise ValueError(f"audit version {version}: invalid department assumptions")
        try:
            assumption = DepartmentStaffingAssumption(**values)
        except TypeError as error:
            raise ValueError(f"audit version {version}: invalid assumption fields") from error
        assumption.validate(department)
        parsed[department] = assumption
    return parsed


def _changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> int:
    departments = set(previous) | set(current)
    return sum(
        1
        for department in departments
        for field in set(previous.get(department, {})) | set(current.get(department, {}))
        if previous.get(department, {}).get(field) != current.get(department, {}).get(field)
    )


def verify_assumption_history(
    path: Path,
    current_assumptions: dict[str, DepartmentStaffingAssumption],
) -> AssumptionAuditReport:
    """Validate an append-only hash chain and reconcile its latest approved snapshot."""
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not entries:
        raise ValueError("assumption audit history must not be empty")
    previous_hash: str | None = None
    previous_assumptions: dict[str, Any] = {}
    latest_changes = 0
    for expected_version, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"audit version {expected_version}: entry must be an object")
        if entry.get("version") != expected_version:
            raise ValueError("assumption audit versions must be sequential")
        if entry.get("previous_entry_hash") != previous_hash:
            raise ValueError(f"audit version {expected_version}: previous hash mismatch")
        if entry.get("decision") != "approved_for_synthetic_demo":
            raise ValueError(f"audit version {expected_version}: unsupported decision")
        if (
            not str(entry.get("reviewer_role", "")).strip()
            or not str(entry.get("change_reason", "")).strip()
        ):
            raise ValueError(f"audit version {expected_version}: review metadata is required")
        try:
            effective = datetime.fromisoformat(str(entry["effective_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise ValueError(f"audit version {expected_version}: invalid effective_at") from error
        if effective.tzinfo is None:
            raise ValueError(
                f"audit version {expected_version}: effective_at must include timezone"
            )
        _validate_assumptions(entry.get("assumptions"), expected_version)
        actual_hash = _entry_hash(entry)
        if entry.get("entry_hash") != actual_hash:
            raise ValueError(f"audit version {expected_version}: entry hash mismatch")
        latest_changes = _changed_fields(previous_assumptions, entry["assumptions"])
        previous_assumptions = entry["assumptions"]
        previous_hash = actual_hash

    current_payload = _assumption_payload(current_assumptions)
    matches = current_payload == previous_assumptions
    return AssumptionAuditReport(
        status="verified" if matches else "action_required",
        versions=len(entries),
        latest_version=int(entries[-1]["version"]),
        latest_effective_at=str(entries[-1]["effective_at"]),
        latest_entry_hash=str(entries[-1]["entry_hash"]),
        current_assumptions_match=matches,
        changed_fields_in_latest_version=latest_changes,
    )
