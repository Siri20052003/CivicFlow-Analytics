CREATE TABLE dim_case (
    case_id TEXT PRIMARY KEY,
    opened_at TIMESTAMPTZ NOT NULL,
    department TEXT NOT NULL,
    service_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    channel TEXT NOT NULL,
    district SMALLINT NOT NULL CHECK (district BETWEEN 1 AND 10),
    assigned_team TEXT NOT NULL,
    target_hours INTEGER NOT NULL CHECK (target_hours > 0),
    closed_at TIMESTAMPTZ,
    satisfaction_score SMALLINT CHECK (satisfaction_score BETWEEN 1 AND 5)
);

CREATE TABLE fact_case_status_event (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES dim_case(case_id),
    occurred_at TIMESTAMPTZ NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    assigned_team TEXT NOT NULL
);

CREATE TABLE ingestion_receipt (
    request_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    accepted_cases INTEGER NOT NULL CHECK (accepted_cases >= 0),
    accepted_events INTEGER NOT NULL CHECK (accepted_events >= 0),
    ingested_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_event_case_time
    ON fact_case_status_event(case_id, occurred_at DESC, event_id DESC);
CREATE INDEX idx_case_department_open
    ON dim_case(department, closed_at);

CREATE VIEW current_case_state AS
SELECT c.*, e.to_status AS current_status, e.occurred_at AS status_changed_at
FROM dim_case AS c
JOIN LATERAL (
    SELECT latest.to_status, latest.occurred_at
    FROM fact_case_status_event AS latest
    WHERE latest.case_id = c.case_id
    ORDER BY latest.occurred_at DESC, latest.event_id DESC
    LIMIT 1
) AS e ON TRUE;
