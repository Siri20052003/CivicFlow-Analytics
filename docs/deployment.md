# API deployment guide

The API image is an operational portfolio reference, not a claim of unrestricted internet readiness. It demonstrates the application boundary, transactional ingestion, runtime probes, telemetry, and repeatable load validation. A real agency deployment must add an approved identity and network boundary.

## Container contract

| Setting | Default | Purpose |
|---|---:|---|
| `CIVICFLOW_DB` | `data/generated/civicflow.db` | Persistent SQLite warehouse path |
| `CIVICFLOW_HOST` | `0.0.0.0` | Bind address |
| `CIVICFLOW_PORT` | `8000` | HTTP port |

Mount the directory containing `CIVICFLOW_DB` on durable storage. The container runs as the unprivileged `civicflow` user and the image health check calls `/health/ready`.

## Production topology

1. Terminate TLS at a managed load balancer or API gateway.
2. Enforce agency SSO/OIDC and role-based scopes at that gateway; the demo service deliberately contains no invented identity provider.
3. Forward or generate `x-request-id` and collect application logs alongside the gateway access log.
4. Scrape `/metrics` from a private monitoring network. Alert on readiness failures, elevated `409` conflicts, and HTTP error-rate changes.
5. Back up the database volume and test restoration before accepting authoritative records.

SQLite uses `BEGIN IMMEDIATE` to serialize writers and protect idempotency receipts. Run a single API replica per SQLite volume. For horizontal scaling, migrate the three warehouse tables and uniqueness constraints to PostgreSQL, retain the transaction boundary around case rows, event rows, and the ingestion receipt, then load test the selected connection-pool and lock settings.

## Release verification

The CI workflow executes linting, formatting, unit and API integration tests, a 5,000-case analytical pipeline, dashboard health validation, a 250-request concurrent API probe with exact replay checks, and both dashboard and API container builds. Deployment should pin an image digest produced from a passing `main` commit and roll back if readiness does not stabilize.
