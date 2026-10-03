# Executive snapshot retention

`civicflow-snapshot` turns a completed CivicFlow pipeline run into an immutable executive evidence snapshot. Each snapshot contains the staffing plan, rolling forecast backtest, forecast control result, assumption audit result, SLA-risk model report, and a self-contained briefing. A manifest records every byte count and SHA-256 digest.

## Scheduled operation

Run the analytics pipeline first, then invoke the snapshot command from a scheduler that already provides durable storage:

```bash
civicflow --cases 5000 --seed 20260922 --output-dir /srv/civicflow/current
civicflow-snapshot \
  --source-dir /srv/civicflow/current \
  --snapshot-dir /srv/civicflow/snapshots
```

Snapshot IDs are UTC timestamps. Creation writes into a temporary sibling directory and publishes with one rename, so readers never see a partial bundle. Reusing an ID is rejected rather than overwriting evidence.

The packaged policy retains snapshots for 90 days and always protects the newest 12, even when they are older. Retention is a dry run unless `--apply-retention` is supplied. Every candidate is integrity-checked before deletion; missing, extra, linked, or modified files stop the entire operation before any deletion begins.

```bash
# Review retention-report.json without deleting anything.
civicflow-snapshot --source-dir /srv/civicflow/current \
  --snapshot-dir /srv/civicflow/snapshots

# Use only after an operator reviews the policy and durable backup boundary.
civicflow-snapshot --source-dir /srv/civicflow/current \
  --snapshot-dir /srv/civicflow/snapshots --apply-retention
```

`retention-report.json` records each retain/delete decision and the reason. Keep the snapshot directory on versioned or replicated storage, restrict write access to the scheduled identity, alert on integrity failures, and copy the report to the organization’s audit-log destination.

All packaged reports are synthetic demonstrations. Retention controls protect evidence integrity; they do not make model or staffing outputs suitable for automatic public-service decisions.
