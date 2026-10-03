import json
from datetime import UTC, datetime, timedelta

import pytest

from civicflow.snapshots import (
    BRIEFING_NAME,
    MANIFEST_NAME,
    RETENTION_REPORT_NAME,
    SOURCE_ARTIFACTS,
    SnapshotRetentionPolicy,
    create_snapshot,
    enforce_retention,
    load_retention_policy,
    verify_snapshot,
)

NOW = datetime(2026, 10, 3, 13, tzinfo=UTC)


def source_reports(tmp_path):
    source = tmp_path / "generated"
    source.mkdir()
    payloads = {
        "staffing_scenario_report.json": {
            "reports": [
                {
                    "current_fte": 44,
                    "required_fte": 50,
                    "fte_gap": 6,
                    "annualized_cost_delta": 552000,
                }
            ]
        },
        "staffing_backtest_report.json": {
            "weighted_absolute_percentage_error": 0.12,
            "planning_coverage_rate": 0.85,
        },
        "forecast_monitoring_report.json": {"status": "pass"},
        "assumption_audit_report.json": {"status": "verified"},
        "sla_prediction_report.json": {"drift_level": "stable", "roc_auc": 0.61},
    }
    for name in SOURCE_ARTIFACTS:
        (source / name).write_text(json.dumps(payloads[name]))
    return source


def test_snapshot_is_atomic_complete_and_verifiable(tmp_path) -> None:
    source = source_reports(tmp_path)
    snapshot = create_snapshot(source, tmp_path / "snapshots", created_at=NOW)
    manifest = verify_snapshot(snapshot)

    assert snapshot.name == "20261003T130000Z"
    assert manifest["synthetic_data"] is True
    assert set(manifest["files"]) == {*SOURCE_ARTIFACTS, BRIEFING_NAME}
    assert "Synthetic demonstration" in (snapshot / BRIEFING_NAME).read_text()
    assert not list((tmp_path / "snapshots").glob(".*"))


@pytest.mark.parametrize("change", ["tamper", "missing", "extra"])
def test_verification_rejects_changed_snapshot_contents(tmp_path, change) -> None:
    snapshot = create_snapshot(source_reports(tmp_path), tmp_path / "snapshots", created_at=NOW)
    if change == "tamper":
        (snapshot / BRIEFING_NAME).write_text("changed")
    elif change == "missing":
        (snapshot / BRIEFING_NAME).unlink()
    else:
        (snapshot / "untracked.txt").write_text("unexpected")

    with pytest.raises(ValueError, match="integrity|contents"):
        verify_snapshot(snapshot)


def test_duplicate_snapshot_id_cannot_be_overwritten(tmp_path) -> None:
    source = source_reports(tmp_path)
    root = tmp_path / "snapshots"
    create_snapshot(source, root, created_at=NOW)

    with pytest.raises(FileExistsError, match="already exists"):
        create_snapshot(source, root, created_at=NOW)


def test_policy_loader_is_strict(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text('{"retention_days": 90, "minimum_snapshots": 12}')
    assert load_retention_policy(valid) == SnapshotRetentionPolicy(90, 12)
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"retention_days": 90, "minimum_snapshots": 12, "unknown": true}')

    with pytest.raises(ValueError, match="exactly"):
        load_retention_policy(invalid)


def test_retention_dry_run_and_apply_preserve_floor_and_recent_snapshots(tmp_path) -> None:
    source = source_reports(tmp_path)
    root = tmp_path / "snapshots"
    old = create_snapshot(source, root, created_at=NOW - timedelta(days=120))
    floor = create_snapshot(source, root, created_at=NOW - timedelta(days=100))
    recent = create_snapshot(source, root, created_at=NOW - timedelta(days=10))
    policy = SnapshotRetentionPolicy(retention_days=90, minimum_snapshots=2)
    (root / RETENTION_REPORT_NAME).write_text('{"previous_run": true}')

    dry_run = enforce_retention(root, policy, evaluated_at=NOW)
    assert dry_run.mode == "dry_run"
    assert old.exists()
    assert [item.snapshot_id for item in dry_run.decisions if item.decision == "delete"] == [
        old.name
    ]
    assert {item.snapshot_id for item in dry_run.decisions if item.decision == "retain"} == {
        floor.name,
        recent.name,
    }

    applied = enforce_retention(root, policy, evaluated_at=NOW, apply=True)
    assert applied.deleted_snapshot_ids == [old.name]
    assert not old.exists()
    assert floor.exists() and recent.exists()


def test_corruption_blocks_retention_before_any_deletion(tmp_path) -> None:
    source = source_reports(tmp_path)
    root = tmp_path / "snapshots"
    oldest = create_snapshot(source, root, created_at=NOW - timedelta(days=140))
    corrupt = create_snapshot(source, root, created_at=NOW - timedelta(days=130))
    create_snapshot(source, root, created_at=NOW - timedelta(days=5))
    manifest = json.loads((corrupt / MANIFEST_NAME).read_text())
    manifest["files"][BRIEFING_NAME]["bytes"] += 1
    (corrupt / MANIFEST_NAME).write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="integrity"):
        enforce_retention(
            root,
            SnapshotRetentionPolicy(retention_days=90, minimum_snapshots=1),
            evaluated_at=NOW,
            apply=True,
        )
    assert oldest.exists()
