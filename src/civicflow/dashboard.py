"""Interactive municipal operations console backed by the generated warehouse."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from civicflow.geography import MIN_CLOSED_CASES, district_metric_from_counts
from civicflow.prediction import (
    PredictionReport,
    score_open_cases_frame,
    train_sla_risk_model_frame,
)
from civicflow.staffing import (
    StaffingScenario,
    StaffingScenarioReport,
    build_staffing_scenario_frame,
)
from civicflow.synthetic import generate_cases, generate_status_events
from civicflow.warehouse import load_warehouse

DEFAULT_DATABASE = Path(os.environ.get("CIVICFLOW_DB", "data/generated/civicflow.db"))


def ensure_demo_database(database: Path) -> None:
    """Create deterministic demo data only when a warehouse has not been supplied."""
    if database.exists():
        return
    as_of = datetime.now(UTC).replace(microsecond=0)
    cases = generate_cases(5_000, seed=20260923, as_of=as_of)
    events = generate_status_events(cases, as_of=as_of)
    load_warehouse(database, cases, events)


def load_dashboard_frames(database: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read case and event grains into typed frames without mutating the warehouse."""
    with sqlite3.connect(database) as connection:
        cases = pd.read_sql_query("SELECT * FROM current_case_state", connection)
        events = pd.read_sql_query(
            """SELECT e.*, c.department, c.district, c.target_hours
            FROM fact_case_status_event AS e
            JOIN dim_case AS c USING (case_id)""",
            connection,
        )
    for column in ("opened_at", "closed_at", "status_changed_at"):
        cases[column] = pd.to_datetime(cases[column], utc=True, format="ISO8601")
    events["occurred_at"] = pd.to_datetime(events["occurred_at"], utc=True, format="ISO8601")
    return cases, events


def filter_frames(
    cases: pd.DataFrame,
    events: pd.DataFrame,
    *,
    departments: list[str],
    districts: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    filtered = cases[
        cases["department"].isin(departments) & cases["district"].isin(districts)
    ].copy()
    return filtered, events[events["case_id"].isin(filtered["case_id"])].copy()


def build_dashboard_metrics(
    cases: pd.DataFrame, events: pd.DataFrame, *, as_of: datetime
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
    """Calculate display metrics from filtered grains for testable UI behavior."""
    closed = cases[cases["closed_at"].notna()].copy()
    closed["resolution_hours"] = (
        closed["closed_at"] - closed["opened_at"]
    ).dt.total_seconds() / 3600
    closed["met_sla"] = closed["resolution_hours"] <= closed["target_hours"]

    active = cases[cases["closed_at"].isna()].copy()
    active["age_hours"] = (pd.Timestamp(as_of) - active["opened_at"]).dt.total_seconds() / 3600
    active["breached"] = active["age_hours"] > active["target_hours"]

    pivots = events.pivot_table(
        index=["case_id", "department"],
        columns="to_status",
        values="occurred_at",
        aggfunc="min",
    ).reset_index()
    completed = pivots.dropna(subset=["open", "in_progress", "resolved"]).copy()
    completed["first_action_hours"] = (
        completed["in_progress"] - completed["open"]
    ).dt.total_seconds() / 3600
    completed["active_work_hours"] = (
        completed["resolved"] - completed["in_progress"]
    ).dt.total_seconds() / 3600
    cycle = (
        completed.groupby("department", as_index=False)
        .agg(
            completed_cases=("case_id", "count"),
            median_first_action_hours=("first_action_hours", "median"),
            p90_first_action_hours=("first_action_hours", lambda values: values.quantile(0.9)),
            median_active_work_hours=("active_work_hours", "median"),
        )
        .round(2)
    )

    closed["opened_month"] = closed["opened_at"].dt.strftime("%Y-%m")
    cohorts = (
        closed.groupby(["opened_month", "department"], as_index=False)
        .agg(
            closed_cases=("case_id", "count"),
            compliance_rate=("met_sla", "mean"),
            median_resolution_hours=("resolution_hours", "median"),
        )
        .round({"compliance_rate": 4, "median_resolution_hours": 2})
    )

    citywide_rate = float(closed["met_sla"].mean()) if len(closed) else 0.0
    district_metrics = []
    for district, rows in cases.groupby("district"):
        district_closed = closed[closed["district"] == district]
        district_metrics.append(
            district_metric_from_counts(
                int(district),
                total_cases=len(rows),
                closed_cases=len(district_closed),
                compliant_cases=int(district_closed["met_sla"].sum()),
                citywide_compliance_rate=citywide_rate,
            )
        )
    districts = pd.DataFrame([asdict(metric) for metric in district_metrics])
    kpis: dict[str, float | int] = {
        "total_cases": len(cases),
        "open_cases": len(active),
        "current_breaches": int(active["breached"].sum()),
        "compliance_rate": round(float(closed["met_sla"].mean()), 4) if len(closed) else 0.0,
    }
    return cycle, cohorts, districts, kpis


def build_risk_view(
    all_cases: pd.DataFrame, filtered_cases: pd.DataFrame
) -> tuple[PredictionReport, pd.DataFrame, pd.DataFrame]:
    """Train on the full history, then rank only the currently selected open cases."""
    model, report = train_sla_risk_model_frame(all_cases)
    scores = score_open_cases_frame(filtered_cases, model, limit=100)
    effects = pd.DataFrame([asdict(effect) for effect in report.top_feature_effects])
    return report, scores, effects


def build_staffing_view(
    cases: pd.DataFrame,
    *,
    departments: list[str],
    demand_multiplier: float,
    service_level_target: float,
    shrinkage_rate: float,
) -> tuple[StaffingScenarioReport, pd.DataFrame]:
    """Build a citywide staffing scenario for the selected departments."""
    planning_cases = cases[cases["department"].isin(departments)].copy()
    scenario = StaffingScenario(
        name="Executive plan",
        demand_multiplier=demand_multiplier,
        service_level_target=service_level_target,
        shrinkage_rate=shrinkage_rate,
    )
    report = build_staffing_scenario_frame(planning_cases, scenario)
    return report, pd.DataFrame([asdict(item) for item in report.forecasts])


def render() -> None:
    st.set_page_config(page_title="CivicFlow Operations", page_icon="🏙️", layout="wide")
    st.title("CivicFlow Operations Console")
    st.caption("Synthetic municipal service data · definitions are documented and reproducible")

    ensure_demo_database(DEFAULT_DATABASE)
    cases, events = load_dashboard_frames(DEFAULT_DATABASE)
    department_options = sorted(cases["department"].unique())
    departments = st.sidebar.multiselect(
        "Departments", department_options, default=department_options
    )
    districts = st.sidebar.multiselect(
        "Districts", sorted(cases["district"].unique()), default=sorted(cases["district"].unique())
    )
    st.sidebar.subheader("Staffing scenario")
    demand_multiplier = st.sidebar.slider(
        "Demand multiplier", min_value=0.80, max_value=1.40, value=1.00, step=0.05
    )
    service_level_target = st.sidebar.slider(
        "Service-level target", min_value=0.80, max_value=0.99, value=0.90, step=0.01
    )
    shrinkage_rate = st.sidebar.slider(
        "Non-casework time", min_value=0.00, max_value=0.40, value=0.20, step=0.05
    )
    filtered_cases, filtered_events = filter_frames(
        cases, events, departments=departments, districts=districts
    )
    if filtered_cases.empty:
        st.warning("No cases match the selected filters.")
        return

    as_of = datetime.now(UTC).replace(microsecond=0)
    cycle, cohorts, district_metrics, kpis = build_dashboard_metrics(
        filtered_cases, filtered_events, as_of=as_of
    )
    columns = st.columns(4)
    columns[0].metric("Cases", f"{kpis['total_cases']:,}")
    columns[1].metric("Open backlog", f"{kpis['open_cases']:,}")
    columns[2].metric("Current SLA breaches", f"{kpis['current_breaches']:,}")
    columns[3].metric("Closed-case compliance", f"{kpis['compliance_rate']:.1%}")

    st.subheader("Executive staffing scenario")
    st.caption(
        "Citywide department demand using complete historical weeks, explicit task-effort "
        "assumptions, and a configurable arrival buffer."
    )
    staffing, staffing_table = build_staffing_view(
        cases,
        departments=departments,
        demand_multiplier=demand_multiplier,
        service_level_target=service_level_target,
        shrinkage_rate=shrinkage_rate,
    )
    staffing_columns = st.columns(4)
    staffing_columns[0].metric("Current FTE", staffing.current_fte)
    staffing_columns[1].metric("Required FTE", staffing.required_fte)
    staffing_columns[2].metric("FTE gap", f"{staffing.fte_gap:+d}")
    staffing_columns[3].metric("Annual cost delta", f"${staffing.annualized_cost_delta:,.0f}")
    st.bar_chart(staffing_table.set_index("department")[["current_fte", "required_fte"]])
    st.dataframe(
        staffing_table[
            [
                "department",
                "expected_weekly_cases",
                "planning_weekly_cases",
                "average_effort_hours",
                "expected_utilization",
                "current_fte",
                "required_fte",
                "fte_gap",
                "capacity_status",
            ]
        ],
        width="stretch",
        hide_index=True,
    )

    st.subheader("SLA breach risk")
    st.caption(
        "Calibrated intake-time probabilities support workload planning; they do not "
        "automatically change case priority or assignment."
    )
    prediction, risk_scores, feature_effects = build_risk_view(cases, filtered_cases)
    risk_columns = st.columns(4)
    risk_columns[0].metric("Holdout ROC AUC", f"{prediction.roc_auc:.3f}")
    risk_columns[1].metric("Brier score", f"{prediction.brier_score:.3f}")
    risk_columns[2].metric("Calibration error", f"{prediction.expected_calibration_error:.3f}")
    risk_columns[3].metric(
        "Risk drift",
        prediction.drift_level.replace("_", " ").title(),
        help=f"Population stability index: {prediction.risk_population_stability_index:.3f}",
    )
    if risk_scores.empty:
        st.info("No open cases match the selected filters.")
    else:
        st.bar_chart(risk_scores["risk_band"].value_counts())
        risk_table = risk_scores.copy()
        risk_table["risk_probability"] = risk_table["risk_probability"].map(
            lambda value: f"{value:.1%}"
        )
        st.dataframe(risk_table, width="stretch", hide_index=True)
    with st.expander("Model drivers and validation"):
        st.caption(
            "Positive coefficients indicate higher modeled risk. The newest chronological "
            "20% of closed cases is reserved for final evaluation."
        )
        st.dataframe(feature_effects, width="stretch", hide_index=True)

    st.subheader("Workflow cycle time")
    cycle_chart = cycle.set_index("department")[
        ["median_first_action_hours", "median_active_work_hours"]
    ]
    st.bar_chart(cycle_chart)
    st.dataframe(cycle, width="stretch", hide_index=True)

    st.subheader("Monthly intake cohorts")
    cohort_chart = cohorts.pivot(
        index="opened_month", columns="department", values="compliance_rate"
    )
    st.line_chart(cohort_chart)
    st.dataframe(cohorts, width="stretch", hide_index=True)

    st.subheader("District service access")
    st.caption(
        "Fictional district geography. Outcome rates are hidden for cohorts with fewer than "
        f"{MIN_CLOSED_CASES} closed cases."
    )
    st.map(
        district_metrics,
        latitude="latitude",
        longitude="longitude",
        size="total_cases",
        zoom=10,
    )
    published = district_metrics[district_metrics["compliance_rate"].notna()]
    if not published.empty:
        st.bar_chart(published.set_index("district_name")["compliance_rate"])
    district_table = district_metrics[
        [
            "district",
            "district_name",
            "total_cases",
            "closed_cases",
            "cases_per_1000_residents",
            "compliance_rate",
            "confidence_lower",
            "confidence_upper",
            "comparison",
            "suppression_reason",
        ]
    ]
    st.dataframe(district_table, width="stretch", hide_index=True)

    with st.expander("Metric guardrails"):
        st.markdown(
            "Closed-case compliance excludes open cases. Current breaches compare active age "
            "with the case priority target. Cycle time requires open, in-progress, and "
            "resolved events. District comparisons use 95% Wilson intervals and never infer "
            "demographic fairness from geography. SLA risk uses only fields known at intake; "
            "closure, satisfaction, current status, and elapsed-resolution fields are excluded."
        )


def main() -> None:
    """Launch the dashboard through Streamlit's supported command runner."""
    from streamlit.web import cli as stcli

    stcli.main_run([__file__, "--server.headless=true"])


if __name__ == "__main__":
    render()
