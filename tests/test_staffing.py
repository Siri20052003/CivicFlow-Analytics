from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from civicflow.prediction import cases_to_frame
from civicflow.staffing import (
    DEFAULT_CURRENT_FTE,
    DepartmentStaffingAssumption,
    StaffingScenario,
    build_default_staffing_suite,
    build_staffing_backtest_frame,
    build_staffing_scenario_frame,
    load_department_assumptions,
)
from civicflow.synthetic import generate_cases


@pytest.fixture(scope="module")
def case_frame() -> pd.DataFrame:
    cases = generate_cases(5_000, seed=20260926, as_of=datetime(2026, 9, 26, tzinfo=UTC))
    return cases_to_frame(cases)


def test_staffing_forecast_reconciles_and_exposes_assumptions(case_frame) -> None:
    scenario = StaffingScenario(name="Plan", service_level_target=0.90)
    report = build_staffing_scenario_frame(case_frame, scenario)

    assert report.current_fte == sum(DEFAULT_CURRENT_FTE.values())
    assert report.required_fte == sum(item.required_fte for item in report.forecasts)
    assert report.fte_gap == report.required_fte - report.current_fte
    assert report.annualized_cost_delta == report.fte_gap * scenario.loaded_cost_per_fte_year
    assert len(report.forecasts) == 5
    assert all(item.observed_weeks == 12 for item in report.forecasts)
    assert all(
        item.planning_weekly_cases >= item.expected_weekly_cases for item in report.forecasts
    )
    assert all(item.average_effort_hours > 0 for item in report.forecasts)
    assert all(
        item.capacity_status in {"shortfall", "balanced", "reserve"} for item in report.forecasts
    )


def test_higher_demand_and_service_target_do_not_reduce_required_staff(case_frame) -> None:
    baseline = build_staffing_scenario_frame(
        case_frame, StaffingScenario(name="Baseline", service_level_target=0.85)
    )
    surge = build_staffing_scenario_frame(
        case_frame,
        StaffingScenario(name="Surge", demand_multiplier=1.20, service_level_target=0.95),
    )
    baseline_required = {item.department: item.required_fte for item in baseline.forecasts}
    assert surge.required_fte >= baseline.required_fte
    assert all(item.required_fte >= baseline_required[item.department] for item in surge.forecasts)


def test_default_suite_contains_distinct_executive_scenarios(case_frame) -> None:
    suite = build_default_staffing_suite(case_frame)
    assert [report.scenario.name for report in suite.reports] == [
        "Baseline",
        "Demand surge",
        "High assurance",
    ]
    assert suite.reports[1].required_fte >= suite.reports[0].required_fte
    assert suite.reports[2].required_fte >= suite.reports[0].required_fte


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (StaffingScenario(name="", service_level_target=0.9), "name"),
        (StaffingScenario(name="Bad", service_level_target=1.0), "service_level_target"),
        (StaffingScenario(name="Bad", shrinkage_rate=0.9), "shrinkage_rate"),
        (StaffingScenario(name="Bad", history_weeks=4), "history_weeks"),
    ],
)
def test_invalid_staffing_assumptions_are_rejected(case_frame, scenario, message) -> None:
    with pytest.raises(ValueError, match=message):
        build_staffing_scenario_frame(case_frame, scenario)


def test_unknown_service_requires_an_explicit_effort_assumption(case_frame) -> None:
    changed = case_frame.copy()
    changed.loc[changed.index[0], "service_type"] = "unmapped_request"
    with pytest.raises(ValueError, match="missing effort assumptions"):
        build_staffing_scenario_frame(changed, StaffingScenario(name="Plan"))


def test_rolling_backtest_reports_reconciled_department_accuracy(case_frame) -> None:
    report = build_staffing_backtest_frame(
        case_frame, StaffingScenario(name="Backtest", history_weeks=8), validation_weeks=4
    )

    assert report.validation_weeks == 4
    assert report.validation_start < report.validation_end
    assert len(report.departments) == 5
    assert all(item.validation_weeks == 4 for item in report.departments)
    assert report.mean_absolute_error >= 0
    assert report.weighted_absolute_percentage_error >= 0
    assert 0 <= report.planning_coverage_rate <= 1


def test_backtest_rejects_short_validation_and_history(case_frame) -> None:
    with pytest.raises(ValueError, match="at least 4"):
        build_staffing_backtest_frame(
            case_frame, StaffingScenario(name="Backtest"), validation_weeks=3
        )
    recent = case_frame.sort_values("opened_at").tail(100)
    with pytest.raises(ValueError, match="complete weeks"):
        build_staffing_backtest_frame(
            recent, StaffingScenario(name="Backtest", history_weeks=8), validation_weeks=4
        )


def test_department_assumption_file_changes_capacity_and_cost(case_frame, tmp_path) -> None:
    source = Path("src/civicflow/staffing_assumptions.json")
    assumptions = load_department_assumptions(source)
    report = build_staffing_scenario_frame(
        case_frame,
        StaffingScenario(name="Configured"),
        department_assumptions=assumptions,
    )

    assert report.current_fte == sum(item.current_fte for item in assumptions.values())
    assert report.annualized_cost_delta == sum(
        item.annualized_cost_delta for item in report.forecasts
    )
    water = next(item for item in report.forecasts if item.department == "Water Utilities")
    assert water.annualized_cost_delta == water.fte_gap * 102_000

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"Public Works": {"current_fte": 0}}', encoding="utf-8")
    with pytest.raises(ValueError, match="contain exactly"):
        load_department_assumptions(invalid)


def test_current_fte_and_department_assumptions_are_mutually_exclusive(case_frame) -> None:
    assumptions = {
        department: DepartmentStaffingAssumption(1, 40, 0.2, 90_000)
        for department in DEFAULT_CURRENT_FTE
    }
    with pytest.raises(ValueError, match="not both"):
        build_staffing_scenario_frame(
            case_frame,
            StaffingScenario(name="Invalid"),
            current_fte=DEFAULT_CURRENT_FTE,
            department_assumptions=assumptions,
        )
