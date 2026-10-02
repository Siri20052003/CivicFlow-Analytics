# Forecast governance

CivicFlow treats staffing forecasts as decision support, not automated hiring instructions. The
governance layer makes two operational questions reproducible: whether recent forecast behavior is
within an approved review policy, and whether the active staffing assumptions match the latest
reviewed synthetic-demo snapshot.

## Monitoring policy

`forecast_monitoring.json` defines four independently reviewable limits: maximum WAPE, maximum
absolute bias, minimum planning coverage, and minimum validation weeks. The pipeline evaluates the
overall backtest and every department. A single failed check produces `action_required`; the result
does not silently retrain a model or change staffing levels.

The generated `forecast_monitoring_report.json` records actual values, thresholds, comparison
directions, and pass/action status. Threshold changes therefore go through ordinary source review
instead of being hidden in dashboard code.

## Assumption audit chain

`staffing_assumption_history.jsonl` is append-only. Each entry contains the complete assumption
snapshot, a UTC effective time, reviewer role, decision, rationale, the previous entry hash, and its
own SHA-256 hash. Pipeline validation recomputes the chain and rejects altered history, missing
review metadata, skipped versions, invalid assumptions, or a current configuration that does not
match the latest approved snapshot.

The included decisions are explicitly labeled `approved_for_synthetic_demo`. They demonstrate a
control workflow and do not represent approval by a real municipality, finance team, or labor
organization. A production process should replace the local JSONL file with an access-controlled
approval service while retaining the same validation boundary.
