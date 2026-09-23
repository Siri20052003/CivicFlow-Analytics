"""Interactive municipal operations console backed by the generated warehouse."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

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
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
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
    kpis: dict[str, float | int] = {
        "total_cases": len(cases),
        "open_cases": len(active),
        "current_breaches": int(active["breached"].sum()),
        "compliance_rate": round(float(closed["met_sla"].mean()), 4) if len(closed) else 0.0,
    }
    return cycle, cohorts, kpis


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
    filtered_cases, filtered_events = filter_frames(
        cases, events, departments=departments, districts=districts
    )
    if filtered_cases.empty:
        st.warning("No cases match the selected filters.")
        return

    as_of = datetime.now(UTC).replace(microsecond=0)
    cycle, cohorts, kpis = build_dashboard_metrics(filtered_cases, filtered_events, as_of=as_of)
    columns = st.columns(4)
    columns[0].metric("Cases", f"{kpis['total_cases']:,}")
    columns[1].metric("Open backlog", f"{kpis['open_cases']:,}")
    columns[2].metric("Current SLA breaches", f"{kpis['current_breaches']:,}")
    columns[3].metric("Closed-case compliance", f"{kpis['compliance_rate']:.1%}")

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

    with st.expander("Metric guardrails"):
        st.markdown(
            "Closed-case compliance excludes open cases. Current breaches compare active age "
            "with the case priority target. Cycle time requires open, in-progress, and "
            "resolved events."
        )


def main() -> None:
    """Launch the dashboard through Streamlit's supported command runner."""
    from streamlit.web import cli as stcli

    stcli.main_run([__file__, "--server.headless=true"])


if __name__ == "__main__":
    render()
