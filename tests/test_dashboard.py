from datetime import UTC, datetime

from civicflow.dashboard import (
    build_dashboard_metrics,
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


def test_dashboard_bootstrap_preserves_existing_warehouse(tmp_path) -> None:
    database = tmp_path / "demo.db"
    ensure_demo_database(database)
    original_size = database.stat().st_size
    ensure_demo_database(database)
    assert database.stat().st_size == original_size
