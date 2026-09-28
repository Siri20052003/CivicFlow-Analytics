"""Governed operational extracts and print-ready executive briefings."""

# Long lines inside the self-contained HTML template preserve readable markup.
# ruff: noqa: E501

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html import escape

import pandas as pd

from civicflow.prediction import PredictionReport
from civicflow.staffing import StaffingScenarioReport

CASE_EXPORT_COLUMNS = (
    "case_id",
    "opened_at",
    "department",
    "service_type",
    "priority",
    "channel",
    "district",
    "assigned_team",
    "target_hours",
    "closed_at",
    "current_status",
    "status_changed_at",
    "satisfaction_score",
)
EVENT_EXPORT_COLUMNS = (
    "event_id",
    "case_id",
    "occurred_at",
    "from_status",
    "to_status",
    "assigned_team",
)


@dataclass(frozen=True, slots=True)
class ExecutiveBrief:
    generated_at: str
    departments: list[str]
    districts: list[int]
    total_cases: int
    open_cases: int
    current_breaches: int
    compliance_rate: float
    current_fte: int
    required_fte: int
    fte_gap: int
    annualized_cost_delta: int
    high_risk_queue_cases: int
    risk_drift: str
    priority_actions: list[str]


def build_executive_brief(
    kpis: dict[str, float | int],
    staffing: StaffingScenarioReport,
    risk_scores: pd.DataFrame,
    prediction: PredictionReport,
    *,
    departments: list[str],
    districts: list[int],
    generated_at: datetime,
) -> ExecutiveBrief:
    """Reconcile a concise leadership summary from the filtered dashboard state."""
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    if int(kpis["total_cases"]) < int(kpis["open_cases"]):
        raise ValueError("open cases cannot exceed total cases")
    high_risk = (
        int((risk_scores["risk_band"] == "high").sum()) if "risk_band" in risk_scores.columns else 0
    )
    actions: list[str] = []
    if int(kpis["current_breaches"]) > 0:
        actions.append(
            f"Triage {int(kpis['current_breaches']):,} currently breached open cases, "
            "starting with the calibrated high-risk queue."
        )
    else:
        actions.append("Maintain daily backlog review; no open case is currently beyond target.")
    if staffing.fte_gap > 0:
        largest_gap = max(staffing.forecasts, key=lambda item: (item.fte_gap, item.department))
        actions.append(
            f"Evaluate a phased {staffing.fte_gap}-FTE capacity plan; "
            f"{largest_gap.department} has the largest modeled gap ({largest_gap.fte_gap:+d})."
        )
    else:
        actions.append("Validate schedule coverage before reallocating modeled reserve capacity.")
    if prediction.drift_level == "stable":
        actions.append("Continue scheduled calibration monitoring; current risk drift is stable.")
    else:
        actions.append(
            f"Review model inputs before operational use; drift status is "
            f"{prediction.drift_level.replace('_', ' ')}."
        )
    return ExecutiveBrief(
        generated_at=generated_at.isoformat(),
        departments=sorted(departments),
        districts=sorted(int(district) for district in districts),
        total_cases=int(kpis["total_cases"]),
        open_cases=int(kpis["open_cases"]),
        current_breaches=int(kpis["current_breaches"]),
        compliance_rate=float(kpis["compliance_rate"]),
        current_fte=staffing.current_fte,
        required_fte=staffing.required_fte,
        fte_gap=staffing.fte_gap,
        annualized_cost_delta=staffing.annualized_cost_delta,
        high_risk_queue_cases=high_risk,
        risk_drift=prediction.drift_level,
        priority_actions=actions,
    )


def render_executive_briefing_html(
    brief: ExecutiveBrief, staffing: StaffingScenarioReport
) -> bytes:
    """Create a self-contained, accessible, print-friendly leadership briefing."""
    actions = "".join(f"<li>{escape(action)}</li>" for action in brief.priority_actions)
    staffing_rows = "".join(
        "<tr>"
        f"<td>{escape(row.department)}</td>"
        f"<td>{row.current_fte}</td><td>{row.required_fte}</td>"
        f"<td>{row.fte_gap:+d}</td><td>{row.expected_utilization:.0%}</td>"
        f"<td>{escape(row.capacity_status.title())}</td>"
        "</tr>"
        for row in staffing.forecasts
    )
    departments = ", ".join(escape(value) for value in brief.departments)
    districts = ", ".join(str(value) for value in brief.districts)
    generated = escape(brief.generated_at)
    drift = escape(brief.risk_drift.replace("_", " ").title())
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CivicFlow executive briefing</title>
<style>
:root{{--ink:#102235;--muted:#60758a;--blue:#176b87;--aqua:#dff5f4;--gold:#f4b942}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef3f7;color:var(--ink);font:15px/1.5 Arial,sans-serif}}
main{{max-width:1040px;margin:32px auto;background:white;padding:42px;border-radius:18px;box-shadow:0 12px 32px #17324d1f}}
.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
h1{{font-size:36px;margin:4px 0}} .subtitle,.meta{{color:var(--muted)}}
.notice{{background:#fff7df;border-left:5px solid var(--gold);padding:12px 16px;margin:24px 0}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}}
.card{{background:var(--aqua);padding:18px;border-radius:12px}} .value{{font-size:28px;font-weight:700}}
.label{{color:var(--muted);font-size:13px}} h2{{margin-top:30px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;border-bottom:1px solid #d9e3ea;text-align:left}}
th{{background:#f4f7fa}} footer{{margin-top:30px;color:var(--muted);font-size:13px}}
@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,1fr)}} main{{margin:0;padding:24px;border-radius:0}}}}
@media print{{body{{background:white}} main{{margin:0;box-shadow:none;max-width:none}}}}
</style></head>
<body><main>
<div class="eyebrow">Municipal operations decision support</div>
<h1>CivicFlow executive briefing</h1>
<div class="subtitle">Filtered operational performance, modeled capacity, and governed risk signals</div>
<div class="notice"><strong>Synthetic demonstration.</strong> No real residents, boundaries, or agency outcomes are represented.</div>
<section class="grid" aria-label="Key performance indicators">
<div class="card"><div class="value">{brief.total_cases:,}</div><div class="label">Selected cases</div></div>
<div class="card"><div class="value">{brief.open_cases:,}</div><div class="label">Open backlog</div></div>
<div class="card"><div class="value">{brief.current_breaches:,}</div><div class="label">Current SLA breaches</div></div>
<div class="card"><div class="value">{brief.compliance_rate:.1%}</div><div class="label">Closed-case compliance</div></div>
<div class="card"><div class="value">{brief.required_fte}</div><div class="label">Required FTE</div></div>
<div class="card"><div class="value">{brief.fte_gap:+d}</div><div class="label">Modeled FTE gap</div></div>
<div class="card"><div class="value">${brief.annualized_cost_delta:,.0f}</div><div class="label">Annual capacity delta</div></div>
<div class="card"><div class="value">{drift}</div><div class="label">Risk-model drift</div></div>
</section>
<h2>Leadership actions</h2><ol>{actions}</ol>
<h2>Department capacity</h2>
<table><thead><tr><th>Department</th><th>Current FTE</th><th>Required FTE</th><th>Gap</th><th>Utilization</th><th>Status</th></tr></thead>
<tbody>{staffing_rows}</tbody></table>
<h2>Scope and governance</h2>
<p><strong>Departments:</strong> {departments}<br><strong>Fictional districts:</strong> {districts}<br>
<strong>High-risk cases in displayed queue:</strong> {brief.high_risk_queue_cases}</p>
<p>Closed-case compliance excludes unresolved work. District outcome rates retain small-cohort suppression. Risk scores use intake-time fields only and do not change case priority or assignment. Staffing estimates use synthetic effort and cost assumptions and require human review.</p>
<footer>Generated {generated} · CivicFlow Analytics · briefing schema v1</footer>
</main></body></html>"""
    return document.encode("utf-8")


def _frame_to_csv(frame: pd.DataFrame, columns: tuple[str, ...]) -> bytes:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing export columns: {', '.join(missing)}")
    records = frame.loc[:, columns].copy()
    for column in records.columns:
        if pd.api.types.is_datetime64_any_dtype(records[column]):
            records[column] = records[column].map(
                lambda value: value.isoformat() if pd.notna(value) else ""
            )
        elif pd.api.types.is_object_dtype(records[column]) or isinstance(
            records[column].dtype, pd.StringDtype
        ):
            records[column] = records[column].map(
                lambda value: (
                    f"'{value}"
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                )
            )
    output = io.StringIO(newline="")
    records.to_csv(output, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return output.getvalue().encode("utf-8")


def build_operational_export_bundle(
    cases: pd.DataFrame,
    events: pd.DataFrame,
    brief: ExecutiveBrief,
    briefing_html: bytes,
) -> bytes:
    """Package filtered grains with a checksum manifest and interpretation notes."""
    case_csv = _frame_to_csv(cases, CASE_EXPORT_COLUMNS)
    event_csv = _frame_to_csv(events, EVENT_EXPORT_COLUMNS)
    artifacts = {
        "cases.csv": case_csv,
        "status_events.csv": event_csv,
        "executive_briefing.html": briefing_html,
    }
    manifest = {
        "schema_version": "1.0",
        "generated_at": brief.generated_at,
        "synthetic_data": True,
        "filters": {"departments": brief.departments, "districts": brief.districts},
        "row_counts": {"cases": len(cases), "status_events": len(events)},
        "files": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            for name, content in artifacts.items()
        },
    }
    artifacts["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    artifacts["README.txt"] = (
        b"CivicFlow governed operational extract\n\n"
        b"All records are synthetic. cases.csv is the current-state grain; "
        b"status_events.csv is the immutable lifecycle grain. Filter scope and SHA-256 "
        b"checksums are recorded in manifest.json. Open cases are excluded from final SLA "
        b"compliance, and risk/staffing outputs are decision support requiring human review.\n"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(artifacts.items()):
            archive.writestr(name, content)
    return output.getvalue()
