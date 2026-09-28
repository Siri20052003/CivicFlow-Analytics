import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd

from civicflow.dashboard import (
    build_dashboard_metrics,
    build_risk_view,
    build_staffing_view,
    ensure_demo_database,
    filter_frames,
    load_dashboard_frames,
)
from civicflow.reporting import (
    build_executive_brief,
    build_operational_export_bundle,
    render_executive_briefing_html,
)

AS_OF = datetime(2026, 9, 28, 13, tzinfo=UTC)


def reporting_inputs(tmp_path):
    database = tmp_path / "reporting.db"
    ensure_demo_database(database)
    cases, events = load_dashboard_frames(database)
    department = str(cases.iloc[0]["department"])
    district = int(cases.iloc[0]["district"])
    filtered_cases, filtered_events = filter_frames(
        cases, events, departments=[department], districts=[district]
    )
    _, _, _, kpis = build_dashboard_metrics(filtered_cases, filtered_events, as_of=AS_OF)
    staffing, _ = build_staffing_view(
        cases,
        departments=[department],
        demand_multiplier=1.0,
        service_level_target=0.9,
        shrinkage_rate=0.2,
    )
    prediction, scores, _ = build_risk_view(cases, filtered_cases)
    brief = build_executive_brief(
        kpis,
        staffing,
        scores,
        prediction,
        departments=[department],
        districts=[district],
        generated_at=AS_OF,
    )
    return filtered_cases, filtered_events, staffing, brief


def test_executive_brief_reconciles_filtered_metrics(tmp_path) -> None:
    cases, _, staffing, brief = reporting_inputs(tmp_path)

    assert brief.total_cases == len(cases)
    assert brief.current_fte == staffing.current_fte
    assert brief.required_fte == staffing.required_fte
    assert brief.fte_gap == brief.required_fte - brief.current_fte
    assert len(brief.priority_actions) == 3
    assert brief.generated_at == AS_OF.isoformat()


def test_briefing_html_is_self_contained_printable_and_escaped(tmp_path) -> None:
    _, _, staffing, brief = reporting_inputs(tmp_path)
    unsafe = replace(brief, departments=["<script>alert(1)</script>"])
    html = render_executive_briefing_html(unsafe, staffing).decode()

    assert html.startswith("<!doctype html>")
    assert "@media print" in html
    assert "Synthetic demonstration" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_export_bundle_contains_filtered_grains_and_verified_manifest(tmp_path) -> None:
    cases, events, staffing, brief = reporting_inputs(tmp_path)
    html = render_executive_briefing_html(brief, staffing)
    bundle = build_operational_export_bundle(cases, events, brief, html)

    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "cases.csv",
            "executive_briefing.html",
            "manifest.json",
            "status_events.csv",
        }
        manifest = json.loads(archive.read("manifest.json"))
        exported_cases = pd.read_csv(archive.open("cases.csv"))
        exported_events = pd.read_csv(archive.open("status_events.csv"))
        for name, metadata in manifest["files"].items():
            content = archive.read(name)
            assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
            assert metadata["bytes"] == len(content)

    assert manifest["synthetic_data"] is True
    assert manifest["row_counts"] == {"cases": len(cases), "status_events": len(events)}
    assert set(exported_cases["case_id"]) == set(cases["case_id"])
    assert set(exported_events["case_id"]) <= set(exported_cases["case_id"])


def test_export_neutralizes_spreadsheet_formulas(tmp_path) -> None:
    cases, events, staffing, brief = reporting_inputs(tmp_path)
    cases = cases.copy()
    cases.loc[cases.index[0], "assigned_team"] = '=HYPERLINK("unsafe")'
    html = render_executive_briefing_html(brief, staffing)
    bundle = build_operational_export_bundle(cases, events, brief, html)

    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        exported = pd.read_csv(archive.open("cases.csv"), keep_default_na=False)
    assert exported.iloc[0]["assigned_team"].startswith("'=")
