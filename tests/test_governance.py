from __future__ import annotations

import json
from pathlib import Path

import pytest

from civicflow.governance import (
    ForecastMonitoringThresholds,
    evaluate_forecast_monitoring,
    load_monitoring_thresholds,
    verify_assumption_history,
)
from civicflow.staffing import (
    DepartmentForecastAccuracy,
    StaffingBacktestReport,
    load_department_assumptions,
)


def sample_backtest(*, wape: float = 0.1, coverage: float = 0.9) -> StaffingBacktestReport:
    department = DepartmentForecastAccuracy(
        department="Public Works",
        validation_weeks=4,
        mean_actual_cases=50,
        mean_forecast_cases=51,
        mean_absolute_error=5,
        weighted_absolute_percentage_error=wape,
        mean_bias=1,
        planning_coverage_rate=coverage,
    )
    return StaffingBacktestReport(
        validation_start="2026-08-31",
        validation_end="2026-09-27",
        validation_weeks=4,
        mean_absolute_error=5,
        weighted_absolute_percentage_error=wape,
        mean_bias=1,
        planning_coverage_rate=coverage,
        departments=[department],
    )


def test_monitoring_policy_passes_only_when_all_checks_pass() -> None:
    thresholds = ForecastMonitoringThresholds(0.15, 3, 0.85, 4)
    passing = evaluate_forecast_monitoring(sample_backtest(), thresholds)
    failing = evaluate_forecast_monitoring(sample_backtest(coverage=0.5), thresholds)

    assert passing.status == "pass"
    assert passing.departments[0].status == "pass"
    assert {check.metric for check in passing.checks} == {
        "weighted_absolute_percentage_error",
        "absolute_mean_bias",
        "planning_coverage_rate",
        "validation_weeks",
    }
    assert failing.status == "action_required"
    assert failing.departments[0].status == "action_required"


def test_packaged_thresholds_are_strictly_validated(tmp_path) -> None:
    thresholds = load_monitoring_thresholds(Path("src/civicflow/forecast_monitoring.json"))
    assert thresholds.maximum_wape == 0.15

    invalid = tmp_path / "thresholds.json"
    invalid.write_text(json.dumps({"maximum_wape": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="contain exactly"):
        load_monitoring_thresholds(invalid)


def test_assumption_history_chain_reconciles_to_current_configuration() -> None:
    assumptions = load_department_assumptions(Path("src/civicflow/staffing_assumptions.json"))
    report = verify_assumption_history(
        Path("src/civicflow/staffing_assumption_history.jsonl"), assumptions
    )

    assert report.status == "verified"
    assert report.versions == report.latest_version == 2
    assert report.current_assumptions_match is True
    assert report.changed_fields_in_latest_version == 4
    assert len(report.latest_entry_hash) == 64


def test_assumption_history_detects_tampering_and_stale_configuration(tmp_path) -> None:
    source = Path("src/civicflow/staffing_assumption_history.jsonl")
    assumptions = load_department_assumptions(Path("src/civicflow/staffing_assumptions.json"))
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text(
        source.read_text(encoding="utf-8").replace("1.15", "1.16"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="entry hash mismatch"):
        verify_assumption_history(tampered, assumptions)

    changed = assumptions.copy()
    changed.pop("Water Utilities")
    report = verify_assumption_history(source, changed)
    assert report.status == "action_required"
    assert report.current_assumptions_match is False
