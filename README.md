# CivicFlow Analytics

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![CI](https://github.com/Siri20052003/CivicFlow-Analytics/actions/workflows/ci.yml/badge.svg)

CivicFlow Analytics is an original, production-style portfolio project for measuring how municipal service teams manage resident requests. It combines realistic synthetic cases, explicit domain rules, immutable lifecycle history, a relational operations warehouse, and reproducible SLA reporting.

![Architecture](docs/architecture.svg)

## Why this project exists

Public-service dashboards are only credible when their operational definitions are clear. CivicFlow treats each metric as the output of a validated case ledger instead of starting with visualizations. The generator preserves realistic relationships between priority, SLA target, department, team, closure state, and satisfaction.

No real resident information is used. All records are synthetic and generated locally.

## Current capabilities

- Deterministic generation with an explicit seed and UTC timestamps
- Five municipal departments, fifteen service types, four intake channels, and priority-based SLA targets
- Data-contract checks for unique IDs, valid lifecycles, districts, targets, and satisfaction values
- Department-level case volume, backlog, breach, compliance, and resolution-time metrics
- Active-backlog aging buckets, current breaches, and 24-hour SLA exposure
- Transactional SQLite warehouse with case dimensions, status-event facts, indexes, and a current-state view
- Stable CSV case output and JSON metric output
- Installable CLI, non-root Docker runtime, CI quality gates, and automated tests

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
civicflow --cases 5000 --seed 20260922 --output-dir data/generated
```

Generated artifacts:

- `data/generated/service_cases.csv`: row-level synthetic case ledger
- `data/generated/sla_report.json`: reconciled department metrics
- `data/generated/backlog_report.json`: active workload age and SLA exposure
- `data/generated/civicflow.db`: relational case and lifecycle-event warehouse

## Docker

```bash
docker build -t civicflow-analytics .
docker run --rm -v "$PWD/data/generated:/home/civicflow/output" civicflow-analytics
```

The container runs as an unprivileged user and writes only to the mounted output directory.

## Metric definitions

| Metric | Definition |
|---|---|
| Closed cases | Cases containing a closure timestamp |
| Open cases | Cases without a closure timestamp |
| SLA breach | Closed case where elapsed hours exceed its priority target |
| Compliance rate | Closed cases meeting SLA divided by all closed cases |
| Average resolution | Mean elapsed hours across closed cases |

Open cases are intentionally excluded from final SLA compliance because their outcome is not yet known. The separate backlog report compares each active case's age with its SLA target, exposing current breaches and cases due within 24 hours without corrupting outcome metrics.

## Warehouse model

`dim_case` holds the stable service-request grain. `fact_case_status_event` holds one immutable row per status transition and uses a foreign key back to the case. The `current_case_state` view resolves the latest event for operational queries while retaining the complete history for cycle-time and workflow analysis. Every refresh is validated first and replaced inside one database transaction.

## Roadmap

- Add cohort, equity, and geospatial service-level analysis
- Build an interactive operations dashboard with explainable filters
- Add API ingestion, observability, load tests, and portfolio screenshots

## Responsible use

Synthetic results are demonstrations, not claims about a real city or agency. The generator intentionally avoids names, addresses, coordinates, and demographic attributes. Any future equity analysis will document safeguards against misleading small-group comparisons.

## License

MIT
