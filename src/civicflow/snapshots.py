"""Immutable executive evidence snapshots with integrity and retention controls."""

# Long lines inside the self-contained HTML template preserve readable markup.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

SOURCE_ARTIFACTS = (
    "assumption_audit_report.json",
    "forecast_monitoring_report.json",
    "sla_prediction_report.json",
    "staffing_backtest_report.json",
    "staffing_scenario_report.json",
)
BRIEFING_NAME = "executive_briefing.html"
MANIFEST_NAME = "manifest.json"
RETENTION_REPORT_NAME = "retention-report.json"


@dataclass(frozen=True, slots=True)
class SnapshotRetentionPolicy:
    retention_days: int
    minimum_snapshots: int

    def validate(self) -> None:
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        if not 1 <= self.minimum_snapshots <= 365:
            raise ValueError("minimum_snapshots must be between 1 and 365")


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    snapshot_id: str
    created_at: str
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class RetentionReport:
    evaluated_at: str
    mode: str
    policy: SnapshotRetentionPolicy
    decisions: list[RetentionDecision]
    deleted_snapshot_ids: list[str]


def load_retention_policy(path: Path) -> SnapshotRetentionPolicy:
    """Load a strict, reviewable retention policy."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = {"retention_days", "minimum_snapshots"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"retention policy must contain exactly {sorted(expected)}")
    policy = SnapshotRetentionPolicy(**payload)
    policy.validate()
    return policy


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read required artifact: {path.name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"required artifact must be a JSON object: {path.name}")
    return payload


def _scheduled_briefing(source_dir: Path, generated_at: datetime) -> bytes:
    staffing = _read_json(source_dir / "staffing_scenario_report.json")
    backtest = _read_json(source_dir / "staffing_backtest_report.json")
    monitoring = _read_json(source_dir / "forecast_monitoring_report.json")
    audit = _read_json(source_dir / "assumption_audit_report.json")
    prediction = _read_json(source_dir / "sla_prediction_report.json")
    try:
        baseline = staffing["reports"][0]
        values = {
            "current_fte": int(baseline["current_fte"]),
            "required_fte": int(baseline["required_fte"]),
            "fte_gap": int(baseline["fte_gap"]),
            "cost_delta": int(baseline["annualized_cost_delta"]),
            "wape": float(backtest["weighted_absolute_percentage_error"]),
            "coverage": float(backtest["planning_coverage_rate"]),
            "monitoring": str(monitoring["status"]),
            "audit": str(audit["status"]),
            "drift": str(prediction["drift_level"]),
            "auc": float(prediction["roc_auc"]),
        }
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("source reports do not satisfy the executive briefing contract") from error

    def label(value: str) -> str:
        return escape(value.replace("_", " ").title())

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CivicFlow scheduled executive briefing</title><style>
body{{margin:0;background:#eef3f7;color:#102235;font:15px/1.5 Arial,sans-serif}}main{{max-width:980px;margin:32px auto;background:white;padding:40px;border-radius:16px}}h1{{margin-bottom:4px}}.notice{{border-left:5px solid #f4b942;background:#fff7df;padding:12px 16px;margin:24px 0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card{{background:#dff5f4;padding:17px;border-radius:12px}}.value{{font-size:26px;font-weight:700}}.label{{color:#60758a;font-size:13px}}footer{{margin-top:28px;color:#60758a}}@media(max-width:720px){{.grid{{grid-template-columns:repeat(2,1fr)}}main{{margin:0;border-radius:0}}}}@media print{{body{{background:white}}main{{margin:0;max-width:none}}}}
</style></head><body><main><div>Municipal operations decision support</div><h1>CivicFlow scheduled executive briefing</h1>
<p>Governed capacity, forecast quality, and risk-model monitoring snapshot.</p><div class="notice"><strong>Synthetic demonstration.</strong> Human review is required before any staffing or service decision.</div>
<section class="grid"><div class="card"><div class="value">{values["current_fte"]}</div><div class="label">Current FTE</div></div><div class="card"><div class="value">{values["required_fte"]}</div><div class="label">Required FTE</div></div><div class="card"><div class="value">{values["fte_gap"]:+d}</div><div class="label">Modeled FTE gap</div></div><div class="card"><div class="value">${values["cost_delta"]:,.0f}</div><div class="label">Annual capacity delta</div></div><div class="card"><div class="value">{values["wape"]:.1%}</div><div class="label">Forecast WAPE</div></div><div class="card"><div class="value">{values["coverage"]:.1%}</div><div class="label">Planning coverage</div></div><div class="card"><div class="value">{label(values["monitoring"])}</div><div class="label">Forecast controls</div></div><div class="card"><div class="value">{label(values["audit"])}</div><div class="label">Assumption audit</div></div><div class="card"><div class="value">{values["auc"]:.3f}</div><div class="label">SLA-risk ROC AUC</div></div><div class="card"><div class="value">{label(values["drift"])}</div><div class="label">Risk-model drift</div></div></section>
<h2>Interpretation</h2><p>Capacity values use the baseline synthetic scenario. Forecast metrics come from leakage-safe rolling backtests. Risk-model metrics come from the newest untouched holdout window. A failed control is a review trigger, never an automatic operational action.</p>
<footer>Generated {escape(generated_at.isoformat())} · briefing schema v1</footer></main></body></html>"""
    return document.encode("utf-8")


def _digest(content: bytes) -> dict[str, int | str]:
    return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def create_snapshot(source_dir: Path, snapshot_root: Path, *, created_at: datetime) -> Path:
    """Atomically publish one immutable snapshot and its checksum manifest."""
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    created_at = created_at.astimezone(UTC).replace(microsecond=0)
    snapshot_id = created_at.strftime("%Y%m%dT%H%M%SZ")
    destination = snapshot_root / snapshot_id
    if destination.exists():
        raise FileExistsError(f"snapshot already exists: {snapshot_id}")
    artifacts: dict[str, bytes] = {}
    for name in SOURCE_ARTIFACTS:
        path = source_dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required source artifact is missing or unsafe: {name}")
        artifacts[name] = path.read_bytes()
        _read_json(path)
    artifacts[BRIEFING_NAME] = _scheduled_briefing(source_dir, created_at)
    manifest = {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "synthetic_data": True,
        "files": {name: _digest(content) for name, content in sorted(artifacts.items())},
    }
    snapshot_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=snapshot_root))
    try:
        for name, content in artifacts.items():
            (temporary / name).write_bytes(content)
        (temporary / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    verify_snapshot(destination)
    return destination


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    """Fail closed when a snapshot contains missing, extra, linked, or changed files."""
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        raise ValueError("snapshot path must be a real directory")
    manifest = _read_json(snapshot_dir / MANIFEST_NAME)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("snapshot manifest files must be a non-empty object")
    expected = set(files) | {MANIFEST_NAME}
    actual = {path.name for path in snapshot_dir.iterdir()}
    if actual != expected:
        raise ValueError("snapshot contents do not match the manifest")
    if manifest.get("snapshot_id") != snapshot_dir.name:
        raise ValueError("snapshot directory does not match manifest snapshot_id")
    for name, metadata in files.items():
        path = snapshot_dir / name
        if Path(name).name != name or not path.is_file() or path.is_symlink():
            raise ValueError(f"unsafe snapshot artifact: {name}")
        content = path.read_bytes()
        if not isinstance(metadata, dict) or metadata != _digest(content):
            raise ValueError(f"snapshot integrity check failed: {name}")
    return manifest


def enforce_retention(
    snapshot_root: Path,
    policy: SnapshotRetentionPolicy,
    *,
    evaluated_at: datetime,
    apply: bool = False,
) -> RetentionReport:
    """Plan retention by default; delete only verified candidates with explicit opt-in."""
    policy.validate()
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(UTC).replace(microsecond=0)
    records: list[tuple[Path, datetime, dict[str, Any]]] = []
    if snapshot_root.exists():
        for path in snapshot_root.iterdir():
            if path.name.startswith("."):
                continue
            if path.name == RETENTION_REPORT_NAME and path.is_file() and not path.is_symlink():
                continue
            if not path.is_dir() or path.is_symlink():
                raise ValueError(f"unexpected entry in snapshot root: {path.name}")
            manifest = verify_snapshot(path)
            try:
                created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError) as error:
                raise ValueError(f"snapshot has invalid created_at: {path.name}") from error
            if created.tzinfo is None or created > evaluated_at:
                raise ValueError(f"snapshot has invalid created_at: {path.name}")
            records.append((path, created.astimezone(UTC), manifest))
    records.sort(key=lambda item: (item[1], item[0].name), reverse=True)
    cutoff = evaluated_at - timedelta(days=policy.retention_days)
    decisions: list[RetentionDecision] = []
    candidates: list[Path] = []
    for index, (path, created, _) in enumerate(records):
        if index < policy.minimum_snapshots:
            decision, reason = "retain", "minimum_snapshot_floor"
        elif created >= cutoff:
            decision, reason = "retain", "within_retention_window"
        else:
            decision, reason = "delete", "expired_beyond_snapshot_floor"
            candidates.append(path)
        decisions.append(
            RetentionDecision(
                snapshot_id=path.name,
                created_at=created.isoformat().replace("+00:00", "Z"),
                decision=decision,
                reason=reason,
            )
        )
    deleted: list[str] = []
    if apply:
        for path in candidates:
            verify_snapshot(path)
            shutil.rmtree(path)
            deleted.append(path.name)
    return RetentionReport(
        evaluated_at=evaluated_at.isoformat().replace("+00:00", "Z"),
        mode="apply" if apply else "dry_run",
        policy=policy,
        decisions=decisions,
        deleted_snapshot_ids=deleted,
    )


def write_retention_report(report: RetentionReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
