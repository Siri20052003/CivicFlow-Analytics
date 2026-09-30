"""Operational API for validated, idempotent municipal case ingestion."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from civicflow.auth import (
    ApiAuthenticator,
    ApiCredential,
    ApiPrincipal,
    load_api_credentials,
)
from civicflow.model import CaseStatusEvent, Department, Priority, ServiceCase, Status
from civicflow.validation import ValidationError
from civicflow.warehouse import (
    IngestionConflict,
    ingest_case_batch,
    initialize_warehouse,
)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
BEARER_SECURITY = HTTPBearer(
    auto_error=False,
    bearerFormat="opaque service token",
    description="Service credential configured by SHA-256 digest; tokens are never stored.",
)


class CaseInput(BaseModel):
    """Validated external representation of a service-case snapshot."""

    model_config = ConfigDict(extra="forbid")

    case_id: Annotated[str, Field(min_length=1, max_length=80)]
    opened_at: AwareDatetime
    department: Department
    service_type: Annotated[str, Field(min_length=1, max_length=120)]
    priority: Priority
    channel: Annotated[str, Field(min_length=1, max_length=40)]
    district: Annotated[int, Field(ge=1, le=10)]
    status: Status
    assigned_team: Annotated[str, Field(min_length=1, max_length=120)]
    target_hours: Annotated[int, Field(gt=0, le=720)]
    closed_at: AwareDatetime | None = None
    satisfaction_score: Annotated[int, Field(ge=1, le=5)] | None = None

    def to_domain(self) -> ServiceCase:
        return ServiceCase(**self.model_dump())


class StatusEventInput(BaseModel):
    """Validated external representation of one immutable case transition."""

    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, Field(min_length=1, max_length=100)]
    case_id: Annotated[str, Field(min_length=1, max_length=80)]
    occurred_at: AwareDatetime
    from_status: Status | None
    to_status: Status
    assigned_team: Annotated[str, Field(min_length=1, max_length=120)]

    def to_domain(self) -> CaseStatusEvent:
        return CaseStatusEvent(**self.model_dump())


class IngestionRequest(BaseModel):
    """Bounded append request; request_id is a durable idempotency key."""

    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    cases: Annotated[list[CaseInput], Field(min_length=1, max_length=1_000)]
    events: Annotated[list[StatusEventInput], Field(min_length=1, max_length=4_000)]


class IngestionResponse(BaseModel):
    request_id: str
    accepted_cases: int
    accepted_events: int
    replayed: bool


class ApiMetrics:
    """Small in-process Prometheus collector with bounded label cardinality."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: defaultdict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_seconds = 0.0
        self._accepted_cases = 0
        self._accepted_events = 0
        self._replays = 0
        self._conflicts = 0
        self._auth_failures: defaultdict[str, int] = defaultdict(int)

    def observe_request(self, method: str, path: str, status: int, latency: float) -> None:
        with self._lock:
            self._requests[(method, path, status)] += 1
            self._latency_seconds += latency

    def observe_ingestion(self, cases: int, events: int, replayed: bool) -> None:
        with self._lock:
            if replayed:
                self._replays += 1
            else:
                self._accepted_cases += cases
                self._accepted_events += events

    def observe_conflict(self) -> None:
        with self._lock:
            self._conflicts += 1

    def observe_auth_failure(self, reason: str) -> None:
        with self._lock:
            self._auth_failures[reason] += 1

    def render(self) -> str:
        with self._lock:
            request_rows = sorted(self._requests.items())
            latency = self._latency_seconds
            accepted_cases = self._accepted_cases
            accepted_events = self._accepted_events
            replays = self._replays
            conflicts = self._conflicts
            auth_failures = sorted(self._auth_failures.items())
        lines = [
            "# HELP civicflow_http_requests_total HTTP requests processed.",
            "# TYPE civicflow_http_requests_total counter",
        ]
        for (method, path, status), count in request_rows:
            lines.append(
                "civicflow_http_requests_total"
                f'{{method="{method}",path="{path}",status="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP civicflow_http_request_duration_seconds_total Cumulative request latency.",
                "# TYPE civicflow_http_request_duration_seconds_total counter",
                f"civicflow_http_request_duration_seconds_total {latency:.6f}",
                "# HELP civicflow_ingested_cases_total New cases committed.",
                "# TYPE civicflow_ingested_cases_total counter",
                f"civicflow_ingested_cases_total {accepted_cases}",
                "# HELP civicflow_ingested_events_total New lifecycle events committed.",
                "# TYPE civicflow_ingested_events_total counter",
                f"civicflow_ingested_events_total {accepted_events}",
                "# HELP civicflow_ingestion_replays_total Idempotent request replays.",
                "# TYPE civicflow_ingestion_replays_total counter",
                f"civicflow_ingestion_replays_total {replays}",
                "# HELP civicflow_ingestion_conflicts_total Rejected ingestion conflicts.",
                "# TYPE civicflow_ingestion_conflicts_total counter",
                f"civicflow_ingestion_conflicts_total {conflicts}",
                "# HELP civicflow_auth_failures_total "
                "Rejected authentication or authorization attempts.",
                "# TYPE civicflow_auth_failures_total counter",
            ]
        )
        for reason, count in auth_failures:
            lines.append(f'civicflow_auth_failures_total{{reason="{reason}"}} {count}')
        return "\n".join(lines) + "\n"


def _database_is_ready(database: Path) -> tuple[bool, str]:
    try:
        with sqlite3.connect(f"file:{database}?mode=rw", uri=True, timeout=2) as connection:
            connection.execute("SELECT 1").fetchone()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    except sqlite3.Error as error:
        return False, str(error)
    required = {"dim_case", "fact_case_status_event", "ingestion_receipt"}
    missing = sorted(required - tables)
    return (False, f"missing tables: {', '.join(missing)}") if missing else (True, "ready")


def create_app(
    database: Path | None = None,
    credentials: tuple[ApiCredential, ...] | None = None,
) -> FastAPI:
    """Build an isolated app instance for production or tests."""
    database = database or Path(os.environ.get("CIVICFLOW_DB", "data/generated/civicflow.db"))
    configured_credentials = credentials or load_api_credentials(
        os.environ.get("CIVICFLOW_API_KEYS", "")
    )
    authenticator = ApiAuthenticator(configured_credentials)
    initialize_warehouse(database)
    metrics = ApiMetrics()
    app = FastAPI(
        title="CivicFlow Ingestion API",
        version="1.0.0",
        description="Validated and idempotent municipal service-case ingestion.",
    )
    app.state.database = database
    app.state.metrics = metrics
    app.state.authenticator = authenticator

    def require_scope(required_scope: str):  # type: ignore[no-untyped-def]
        def authorize(
            bearer: Annotated[HTTPAuthorizationCredentials | None, Security(BEARER_SECURITY)],
        ) -> ApiPrincipal:
            if bearer is None:
                metrics.observe_auth_failure("missing")
                raise HTTPException(
                    status_code=401,
                    detail="bearer credential required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            principal, reason = authenticator.authenticate(bearer.credentials)
            if principal is None:
                metrics.observe_auth_failure(reason or "invalid")
                raise HTTPException(
                    status_code=401,
                    detail="invalid or expired bearer credential",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if required_scope not in principal.scopes:
                metrics.observe_auth_failure("insufficient_scope")
                raise HTTPException(
                    status_code=403,
                    detail=f"required scope: {required_scope}",
                )
            return principal

        return authorize

    @app.middleware("http")
    async def operational_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        supplied = request.headers.get("x-request-id", "")
        correlation_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.observe_request(
                request.method, "__unhandled__", 500, time.perf_counter() - started
            )
            raise
        response.headers["x-request-id"] = correlation_id
        route = request.scope.get("route")
        metric_path = getattr(route, "path", "__unmatched__")
        metrics.observe_request(
            request.method,
            metric_path,
            response.status_code,
            time.perf_counter() - started,
        )
        return response

    @app.exception_handler(ValidationError)
    async def validation_error_handler(_request: Request, error: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.get("/health/live", tags=["operations"])
    def liveness() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["operations"])
    def readiness(response: Response) -> dict[str, str]:
        ready, detail = _database_is_ready(database)
        if not ready:
            response.status_code = 503
        return {"status": "ready" if ready else "unavailable", "detail": detail}

    @app.get(
        "/metrics",
        response_class=PlainTextResponse,
        tags=["operations"],
        dependencies=[Depends(require_scope("ops:read"))],
    )
    def prometheus_metrics() -> str:
        return metrics.render()

    @app.post(
        "/v1/case-batches",
        response_model=IngestionResponse,
        status_code=201,
        tags=["ingestion"],
        dependencies=[Depends(require_scope("cases:write"))],
    )
    def ingest(
        payload: IngestionRequest,
        response: Response,
    ) -> IngestionResponse:
        try:
            result = ingest_case_batch(
                database,
                payload.request_id,
                [case.to_domain() for case in payload.cases],
                [event.to_domain() for event in payload.events],
            )
        except IngestionConflict as error:
            metrics.observe_conflict()
            raise HTTPException(status_code=409, detail=str(error)) from error
        metrics.observe_ingestion(result.accepted_cases, result.accepted_events, result.replayed)
        if result.replayed:
            response.status_code = 200
        return IngestionResponse(
            request_id=result.request_id,
            accepted_cases=result.accepted_cases,
            accepted_events=result.accepted_events,
            replayed=result.replayed,
        )

    @app.get(
        "/v1/cases",
        tags=["query"],
        dependencies=[Depends(require_scope("cases:read"))],
    )
    def list_cases(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute("SELECT COUNT(*) FROM current_case_state").fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM current_case_state
                ORDER BY opened_at DESC, case_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return {"total": total, "items": [dict(row) for row in rows]}

    return app


def run() -> None:
    """Run the API service with production-safe network defaults."""
    uvicorn.run(
        "civicflow.api:create_app",
        host=os.environ.get("CIVICFLOW_HOST", "0.0.0.0"),
        port=int(os.environ.get("CIVICFLOW_PORT", "8000")),
        proxy_headers=True,
        factory=True,
    )


if __name__ == "__main__":
    run()
