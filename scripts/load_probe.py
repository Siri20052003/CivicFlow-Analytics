"""Concurrent API smoke probe using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from statistics import quantiles


def payload(index: int) -> bytes:
    case_id = f"LOAD-{index:06d}"
    body = {
        "request_id": f"load-batch-{index:06d}",
        "cases": [
            {
                "case_id": case_id,
                "opened_at": "2026-09-27T12:00:00Z",
                "department": "Transportation",
                "service_type": "Traffic signal inspection",
                "priority": "medium",
                "channel": "mobile",
                "district": (index % 10) + 1,
                "status": "open",
                "assigned_team": "TR-Signals",
                "target_hours": 72,
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
                "assigned_team": "TR-Signals",
            }
        ],
    }
    return json.dumps(body).encode()


def post(base_url: str, index: int, expected_status: int) -> float:
    request = urllib.request.Request(
        f"{base_url}/v1/case-batches",
        data=payload(index),
        headers={"content-type": "application/json", "x-request-id": f"probe-{index}"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != expected_status:
            raise RuntimeError(f"request {index} returned {response.status}")
        if response.headers.get("x-request-id") != f"probe-{index}":
            raise RuntimeError(f"request {index} lost its correlation ID")
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=250)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--max-p95-ms", type=float, default=1_000)
    args = parser.parse_args()
    if args.requests < 20:
        parser.error("--requests must be at least 20 for a meaningful percentile")

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        latencies = list(
            executor.map(
                lambda index: post(args.base_url, index, 201),
                range(args.requests),
            )
        )
    replayed = [post(args.base_url, index, 200) for index in range(10)]
    p95_ms = quantiles(latencies, n=100, method="inclusive")[94] * 1_000
    if p95_ms > args.max_p95_ms:
        raise RuntimeError(f"p95 latency {p95_ms:.1f}ms exceeds {args.max_p95_ms:.1f}ms")

    with urllib.request.urlopen(f"{args.base_url}/health/ready", timeout=5) as response:
        if json.load(response)["status"] != "ready":
            raise RuntimeError("service did not remain ready after load")
    with urllib.request.urlopen(f"{args.base_url}/metrics", timeout=5) as response:
        metrics = response.read().decode()
    if f"civicflow_ingested_cases_total {args.requests}" not in metrics:
        raise RuntimeError("ingestion metric does not reconcile with committed requests")

    print(
        json.dumps(
            {
                "requests": args.requests,
                "concurrency": args.concurrency,
                "replays": len(replayed),
                "p95_ms": round(p95_ms, 2),
                "errors": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
