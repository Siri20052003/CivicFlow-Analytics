from datetime import UTC, datetime

from civicflow.dashboard import (
    build_dashboard_metrics,
    build_risk_view,
    build_staffing_view,
    ensure_demo_database,
    filter_frames,
    load_dashboard_frames,
)


def test_dashboard_bootstrap_and_filtered_metrics(tmp_path) -> None:
    database = tmp_path / "demo.db"
    ensure_demo_database(database)
    cases, events = load_dashboard_frames(database)
    assert len(cases) == 5_000
    assert events["case_id"].nunique() == 5_000

    department = str(cases.iloc[0]["department"])
    district = int(cases.iloc[0]["district"])
    filtered_cases, filtered_events = filter_frames(
        cases, events, departments=[department], districts=[district]
    )
    cycle, cohorts, districts, kpis = build_dashboard_metrics(
        filtered_cases,
        filtered_events,
        as_of=datetime.now(UTC).replace(microsecond=0),
    )
    assert kpis["total_cases"] == len(filtered_cases)
    assert set(filtered_cases["department"]) == {department}
    assert set(filtered_cases["district"]) == {district}
    assert not cycle.empty
    assert not cohorts.empty
    assert len(districts) == 1
    assert districts.iloc[0]["district"] == district

    prediction, scores, effects = build_risk_view(cases, filtered_cases)
    assert prediction.test_cases > 0
    assert scores["risk_probability"].between(0, 1).all()
    assert not effects.empty

    staffing, staffing_table = build_staffing_view(
        cases,
        departments=[department],
        demand_multiplier=1.0,
        service_level_target=0.90,
        shrinkage_rate=0.20,
    )
    assert staffing.current_fte > 0
    assert staffing.required_fte > 0
    assert list(staffing_table["department"]) == [department]


def test_dashboard_bootstrap_preserves_existing_warehouse(tmp_path) -> None:
    database = tmp_path / "demo.db"
    ensure_demo_database(database)
    original_size = database.stat().st_size
    ensure_demo_database(database)
    assert database.stat().st_size == original_size
