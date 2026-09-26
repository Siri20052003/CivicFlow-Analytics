"""Transparent scenario planning for municipal service staffing capacity."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from statistics import NormalDist

import pandas as pd

DEFAULT_CURRENT_FTE = {
    "Code Compliance": 8,
    "Parks & Recreation": 7,
    "Public Works": 10,
    "Transportation": 9,
    "Water Utilities": 10,
}

# Direct-work assumptions for synthetic service types. Elapsed resolution time is deliberately
# excluded because waiting, routing, and resident-response time are not staff labor hours.
EFFORT_HOURS_BY_SERVICE = {
    "blocked_sidewalk": 4.0,
    "illegal_dumping": 5.5,
    "low_pressure": 4.5,
    "noise": 2.0,
    "overgrown_lot": 3.5,
    "playground_repair": 6.0,
    "pothole": 5.0,
    "sewer_backup": 8.0,
    "sign_damage": 4.0,
    "signal_fault": 7.0,
    "street_light": 4.5,
    "trail_damage": 6.5,
    "tree_hazard": 7.5,
    "unsafe_structure": 9.0,
    "water_leak": 7.0,
}


@dataclass(frozen=True, slots=True)
class StaffingScenario:
    name: str
    demand_multiplier: float = 1.0
    service_level_target: float = 0.90
    shrinkage_rate: float = 0.20
    scheduled_hours_per_fte_week: float = 37.5
    loaded_cost_per_fte_year: int = 92_000
    history_weeks: int = 12

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario name is required")
        if not 0.5 <= self.demand_multiplier <= 2:
            raise ValueError("demand_multiplier must be between 0.5 and 2")
        if not 0.5 < self.service_level_target < 1:
            raise ValueError("service_level_target must be between 0.5 and 1")
        if not 0 <= self.shrinkage_rate < 0.8:
            raise ValueError("shrinkage_rate must be between 0 and 0.8")
        if self.scheduled_hours_per_fte_week <= 0:
            raise ValueError("scheduled_hours_per_fte_week must be positive")
        if self.loaded_cost_per_fte_year <= 0:
            raise ValueError("loaded_cost_per_fte_year must be positive")
        if self.history_weeks < 8:
            raise ValueError("history_weeks must be at least 8")


@dataclass(frozen=True, slots=True)
class DepartmentStaffingForecast:
    department: str
    observed_weeks: int
    mean_weekly_arrivals: float
    weekly_arrival_stddev: float
    expected_weekly_cases: float
    planning_weekly_cases: int
    average_effort_hours: float
    planning_workload_hours: float
    current_fte: int
    required_fte: int
    fte_gap: int
    expected_utilization: float
    annualized_cost_delta: int
    capacity_status: str


@dataclass(frozen=True, slots=True)
class StaffingScenarioReport:
    scenario: StaffingScenario
    history_start: str
    history_end: str
    current_fte: int
    required_fte: int
    fte_gap: int
    annualized_cost_delta: int
    forecasts: list[DepartmentStaffingForecast]


@dataclass(frozen=True, slots=True)
class StaffingScenarioSuite:
    reports: list[StaffingScenarioReport]


DEFAULT_SCENARIOS = (
    StaffingScenario(name="Baseline", demand_multiplier=1.0, service_level_target=0.90),
    StaffingScenario(name="Demand surge", demand_multiplier=1.15, service_level_target=0.90),
    StaffingScenario(name="High assurance", demand_multiplier=1.0, service_level_target=0.95),
)


def _prepare_case_history(
    frame: pd.DataFrame, scenario: StaffingScenario
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    required = {"opened_at", "department", "service_type"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing staffing columns: {', '.join(missing)}")
    scenario.validate()
    prepared = frame.copy()
    prepared["opened_at"] = pd.to_datetime(prepared["opened_at"], utc=True, format="ISO8601")
    if prepared.empty:
        raise ValueError("staffing forecast requires case history")
    unknown_services = sorted(set(prepared["service_type"]) - set(EFFORT_HOURS_BY_SERVICE))
    if unknown_services:
        raise ValueError(f"missing effort assumptions: {', '.join(unknown_services)}")

    latest = prepared["opened_at"].max()
    current_week_start = latest.normalize() - pd.Timedelta(days=latest.weekday())
    weeks = pd.date_range(
        end=current_week_start - pd.Timedelta(weeks=1),
        periods=scenario.history_weeks,
        freq="7D",
        tz="UTC",
    )
    history_end = current_week_start
    prepared = prepared[
        (prepared["opened_at"] >= weeks.min()) & (prepared["opened_at"] < history_end)
    ].copy()
    if prepared.empty:
        raise ValueError("staffing forecast has no complete weeks in the history window")
    prepared["week_start"] = prepared["opened_at"].dt.normalize() - pd.to_timedelta(
        prepared["opened_at"].dt.weekday, unit="D"
    )
    return prepared, weeks


def build_staffing_scenario_frame(
    frame: pd.DataFrame,
    scenario: StaffingScenario,
    *,
    current_fte: dict[str, int] | None = None,
) -> StaffingScenarioReport:
    """Convert arrival variability and explicit effort assumptions into FTE requirements."""
    history, weeks = _prepare_case_history(frame, scenario)
    staffing = DEFAULT_CURRENT_FTE if current_fte is None else current_fte
    departments = sorted(history["department"].unique())
    missing_staffing = sorted(set(departments) - set(staffing))
    if missing_staffing:
        raise ValueError(f"missing current FTE assumptions: {', '.join(missing_staffing)}")
    if any(staffing[department] <= 0 for department in departments):
        raise ValueError("current FTE assumptions must be positive")

    service_quantile = NormalDist().inv_cdf(scenario.service_level_target)
    effective_hours = scenario.scheduled_hours_per_fte_week * (1 - scenario.shrinkage_rate)
    forecasts: list[DepartmentStaffingForecast] = []
    for department in departments:
        department_rows = history[history["department"] == department].copy()
        weekly = (
            department_rows.groupby("week_start").size().reindex(weeks, fill_value=0).astype(float)
        )
        mean_arrivals = float(weekly.mean())
        arrival_stddev = float(weekly.std(ddof=1))
        expected_cases = mean_arrivals * scenario.demand_multiplier
        planning_cases = ceil(
            max(
                expected_cases,
                (mean_arrivals + service_quantile * arrival_stddev) * scenario.demand_multiplier,
            )
        )
        effort = float(department_rows["service_type"].map(EFFORT_HOURS_BY_SERVICE).mean())
        planning_hours = planning_cases * effort
        required = ceil(planning_hours / effective_hours)
        present = staffing[department]
        gap = required - present
        expected_hours = expected_cases * effort
        utilization = expected_hours / (present * effective_hours)
        forecasts.append(
            DepartmentStaffingForecast(
                department=department,
                observed_weeks=len(weeks),
                mean_weekly_arrivals=round(mean_arrivals, 2),
                weekly_arrival_stddev=round(arrival_stddev, 2),
                expected_weekly_cases=round(expected_cases, 2),
                planning_weekly_cases=planning_cases,
                average_effort_hours=round(effort, 2),
                planning_workload_hours=round(planning_hours, 2),
                current_fte=present,
                required_fte=required,
                fte_gap=gap,
                expected_utilization=round(utilization, 4),
                annualized_cost_delta=gap * scenario.loaded_cost_per_fte_year,
                capacity_status="shortfall" if gap > 0 else "reserve" if gap < 0 else "balanced",
            )
        )

    current_total = sum(item.current_fte for item in forecasts)
    required_total = sum(item.required_fte for item in forecasts)
    gap_total = required_total - current_total
    return StaffingScenarioReport(
        scenario=scenario,
        history_start=weeks.min().date().isoformat(),
        history_end=(weeks.max() + pd.Timedelta(days=6)).date().isoformat(),
        current_fte=current_total,
        required_fte=required_total,
        fte_gap=gap_total,
        annualized_cost_delta=gap_total * scenario.loaded_cost_per_fte_year,
        forecasts=forecasts,
    )


def build_default_staffing_suite(frame: pd.DataFrame) -> StaffingScenarioSuite:
    return StaffingScenarioSuite(
        reports=[build_staffing_scenario_frame(frame, scenario) for scenario in DEFAULT_SCENARIOS]
    )
