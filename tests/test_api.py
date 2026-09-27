import sqlite3
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from civicflow.api import create_app


def batch(request_id: str = "batch-001", case_id: str = "API-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "cases": [
            {
                "case_id": case_id,
                "opened_at": "2026-09-27T12:00:00Z",
                "department": "Public Works",
                "service_type": "Pothole repair",
                "priority": "high",
                "channel": "web",
                "district": 4,
                "status": "open",
                "assigned_team": "PW-Roads",
                "target_hours": 24,
                "closed_at": None,
                "satisfaction_score": None,
            }
        ],
        "events": [
            {
                "event_id": f"EV-{case_id}",
                "case_id": case_id,
                "occurred_at": "2026-09-27T12:00:00Z",
                "from_status": None,
                "to_status": "open",
                "assigned_team": "PW-Roads",
            }
        ],
    }


def client_for(tmp_path: Path) -> tuple[TestClient, Path]:
    database = tmp_path / "api.db"
    return TestClient(create_app(database)), database


def test_health_and_readiness_include_correlation_id(tmp_path) -> None:
    client, _ = client_for(tmp_path)
    live = client.get("/health/live", headers={"x-request-id": "trace-123"})
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.headers["x-request-id"] == "trace-123"
    assert ready.json() == {"status": "ready", "detail": "ready"}
    assert ready.headers["x-request-id"]


def test_ingestion_commits_case_event_and_receipt_atomically(tmp_path) -> None:
    client, database = client_for(tmp_path)
    response = client.post("/v1/case-batches", json=batch())

    assert response.status_code == 201
    assert response.json() == {
        "request_id": "batch-001",
        "accepted_cases": 1,
        "accepted_events": 1,
        "replayed": False,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dim_case").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fact_case_status_event").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ingestion_receipt").fetchone()[0] == 1


def test_exact_replay_is_idempotent(tmp_path) -> None:
    client, database = client_for(tmp_path)
    payload = batch()
    assert client.post("/v1/case-batches", json=payload).status_code == 201
    replay = client.post("/v1/case-batches", json=payload)

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dim_case").fetchone()[0] == 1


def test_reused_idempotency_key_with_changed_payload_is_conflict(tmp_path) -> None:
    client, _ = client_for(tmp_path)
    assert client.post("/v1/case-batches", json=batch()).status_code == 201
    changed = batch(case_id="API-002")
    response = client.post("/v1/case-batches", json=changed)

    assert response.status_code == 409
    assert "different payload" in response.json()["detail"]


def test_duplicate_entity_in_new_batch_is_conflict(tmp_path) -> None:
    client, _ = client_for(tmp_path)
    assert client.post("/v1/case-batches", json=batch()).status_code == 201
    response = client.post("/v1/case-batches", json=batch(request_id="batch-002"))

    assert response.status_code == 409
    assert "stored entity IDs" in response.json()["detail"]


def test_invalid_lifecycle_rolls_back_entire_batch(tmp_path) -> None:
    client, database = client_for(tmp_path)
    payload = deepcopy(batch())
    payload["events"][0]["to_status"] = "in_progress"  # type: ignore[index]
    response = client.post("/v1/case-batches", json=payload)

    assert response.status_code == 422
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dim_case").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ingestion_receipt").fetchone()[0] == 0


def test_query_endpoint_is_bounded_and_returns_current_state(tmp_path) -> None:
    client, _ = client_for(tmp_path)
    assert client.post("/v1/case-batches", json=batch()).status_code == 201
    response = client.get("/v1/cases?limit=1")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["case_id"] == "API-001"
    assert response.json()["items"][0]["current_status"] == "open"
    assert client.get("/v1/cases?limit=501").status_code == 422


def test_prometheus_metrics_track_commits_replays_and_conflicts(tmp_path) -> None:
    client, _ = client_for(tmp_path)
    payload = batch()
    client.post("/v1/case-batches", json=payload)
    client.post("/v1/case-batches", json=payload)
    client.post("/v1/case-batches", json=batch(request_id="new-key"))
    metrics = client.get("/metrics").text

    assert "civicflow_ingested_cases_total 1" in metrics
    assert "civicflow_ingested_events_total 1" in metrics
    assert "civicflow_ingestion_replays_total 1" in metrics
    assert "civicflow_ingestion_conflicts_total 1" in metrics
