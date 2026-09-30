# API deployment guide

The API image is an operational portfolio reference, not a claim of unrestricted internet readiness. It demonstrates a fail-closed service-authentication boundary, transactional ingestion, runtime probes, telemetry, and repeatable load validation. A real agency deployment must connect this boundary to an approved identity and network architecture.

## Container contract

| Setting | Default | Purpose |
|---|---:|---|
| `CIVICFLOW_DB` | `data/generated/civicflow.db` | Persistent SQLite warehouse path |
| `CIVICFLOW_HOST` | `0.0.0.0` | Bind address |
| `CIVICFLOW_PORT` | `8000` | HTTP port |
| `CIVICFLOW_API_KEYS` | none; required | JSON map of key IDs to SHA-256 token digests, scopes, and optional expiry |

Mount the directory containing `CIVICFLOW_DB` on durable storage. The container runs as the unprivileged `civicflow` user and the image health check calls the intentionally public `/health/ready` endpoint.

## Service credentials

Generate a high-entropy token in a secret manager, calculate its SHA-256 digest, and expose only the JSON digest configuration to the service. Clients receive the original token through the approved secret-distribution channel. Plaintext tokens are rejected in `CIVICFLOW_API_KEYS` and are never retained by the application.

```bash
export CIVICFLOW_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export TOKEN_DIGEST="$(printf %s "$CIVICFLOW_API_KEY" | sha256sum | cut -d' ' -f1)"
export CIVICFLOW_API_KEYS="{\"ingestion-2026-10\":{\"token_sha256\":\"$TOKEN_DIGEST\",\"scopes\":[\"cases:write\"],\"expires_at\":\"2026-11-01T00:00:00Z\"}}"
CIVICFLOW_DB=data/generated/civicflow.db civicflow-api
```

Credentials may carry `cases:read`, `cases:write`, and `ops:read`. Configure overlapping old and new digests during rotation, verify clients have moved, then remove the retired digest. Expired or invalid credentials return `401`; authenticated credentials missing the route scope return `403`. `/health/live` and `/health/ready` remain unauthenticated for orchestrator probes. `/metrics`, case reads, and ingestion require their corresponding scope.

## Production topology

1. Terminate TLS at a managed load balancer or API gateway.
2. Validate agency SSO/OIDC at that gateway, map approved machine identities to narrowly scoped service credentials, and rotate credentials from a managed secret store.
3. Forward or generate `x-request-id` and collect application logs alongside the gateway access log. Never log Authorization headers or plaintext tokens.
4. Scrape the protected `/metrics` endpoint with a dedicated `ops:read` credential. Alert on readiness failures, authentication failures, elevated `409` conflicts, and HTTP error-rate changes.
5. Back up the database volume and test restoration before accepting authoritative records.

SQLite uses `BEGIN IMMEDIATE` to serialize writers and protect idempotency receipts. Run a single API replica per SQLite volume. For horizontal scaling, migrate the three warehouse tables and uniqueness constraints to PostgreSQL, retain the transaction boundary around case rows, event rows, and the ingestion receipt, then load test the selected connection-pool and lock settings.

## Release verification

The CI workflow executes linting, formatting, unit and authenticated API integration tests, a 5,000-case analytical pipeline, dashboard health validation, a 250-request authenticated concurrent API probe with exact replay checks, and both dashboard and API container builds. Deployment should pin an image digest produced from a passing `main` commit and roll back if readiness does not stabilize.
