# PostgreSQL disaster-recovery rehearsal

`civicflow-recovery` proves that a CivicFlow PostgreSQL backup can be restored, reconciled, and returned to a clean state. It is a controlled rehearsal, not a production failover command.

The workflow exports one repeatable-read PostgreSQL snapshot, uses that exact snapshot for both source evidence and a custom-format `pg_dump`, records the backup byte size and SHA-256 digest, creates a new database restricted to the `civicflow_rehearsal_` namespace, restores with `pg_restore`, and independently checks:

- required schema-migration version;
- row counts for cases, lifecycle events, and ingestion receipts;
- deterministic SHA-256 content hashes for all three tables;
- zero orphan lifecycle events;
- recovery-time and recovery-point-age policy objectives; and
- removal of the isolated rehearsal database.

## Runbook

Use a role allowed to read the CivicFlow database and create/drop only rehearsal databases. Do not use the application runtime role if it has broader privileges than necessary.

```bash
export CIVICFLOW_DATABASE_URL='postgresql://civicflow:secret@db.internal:5432/civicflow'
civicflow-recovery \
  --backup-path /srv/civicflow/backups/rehearsal-2026-10-04.dump \
  --report-path /srv/civicflow/reports/rehearsal-2026-10-04.json
```

The command never overwrites a backup and never replaces an existing database. The disposable database name must begin with `civicflow_rehearsal_`, is created only when absent, and is removed after verification. PostgreSQL credentials are passed to client tools through process environment variables rather than command arguments.

The packaged demonstration policy requires migration version 1, restoration plus verification within 120 seconds, and recovery-point age within 300 seconds. Production targets must be approved from service criticality, backup cadence, dataset size, infrastructure capacity, and agency continuity requirements. Archive the JSON report and backup digest in the approved audit system; encrypt backup media, restrict access, test key recovery, and separately rehearse regional failover and application reconnection.

CI exercises the same process against a real PostgreSQL service and requires content hashes to match before the workflow can pass.
