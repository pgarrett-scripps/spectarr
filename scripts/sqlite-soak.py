#!/usr/bin/env python3
"""Stress a running single-container installation through concurrent writes and reads."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import smoke_test


def create_run(index: int, project_id: str, token: str | None) -> dict[str, str]:
    suffix = f"{index:03d}-{uuid.uuid4().hex[:8]}"
    experiment = smoke_test.json_call(
        "POST",
        "/experiments",
        {"project_id": project_id, "name": f"Concurrent experiment {suffix}"},
        token,
    )
    sample = smoke_test.json_call(
        "POST",
        "/samples",
        {"experiment_id": experiment["id"], "name": f"Concurrent sample {suffix}"},
        token,
    )
    run = smoke_test.json_call(
        "POST",
        "/runs",
        {
            "experiment_id": experiment["id"],
            "sample_id": sample["id"],
            "name": f"concurrent-run-{suffix}",
            "source_class": "open",
        },
        token,
    )
    artifact = smoke_test.upload(str(run["id"]), token, smoke_test.FIXTURE)
    smoke_test.json_call("GET", "/runs", token=token)
    return {
        "run_id": str(run["id"]),
        "artifact_id": str(artifact["id"]),
        "sha256": str(artifact["sha256"]),
    }


def enqueue(state_path: Path, workers: int, runs: int) -> dict[str, object]:
    smoke_test.wait_until_ready()
    token = smoke_test.authenticate()
    suffix = uuid.uuid4().hex[:8]
    project = smoke_test.json_call(
        "POST",
        "/projects",
        {"name": f"SQLite soak {suffix}"},
        token,
    )
    records: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(create_run, index, str(project["id"]), token) for index in range(runs)]
        for future in as_completed(futures):
            records.append(future.result())
    state = {
        "project_id": str(project["id"]),
        "fixture_sha256": hashlib.sha256(smoke_test.FIXTURE.read_bytes()).hexdigest(),
        "runs": records,
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    return state


def verify(state_path: Path, timeout_seconds: int) -> dict[str, object]:
    smoke_test.wait_until_ready()
    token = smoke_test.authenticate()
    state = json.loads(state_path.read_text())
    records = state.get("runs", [])
    if not records:
        raise RuntimeError("SQLite soak state contains no runs")
    pending = {record["run_id"] for record in records}
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for run_id in list(pending):
            run = smoke_test.json_call("GET", f"/runs/{run_id}", token=token)
            if run.get("latest_extraction"):
                pending.remove(run_id)
        if pending:
            smoke_test.json_call("GET", "/system/health", token=token)
            time.sleep(1)
    if pending:
        raise RuntimeError(f"Timed out waiting for concurrent extraction: {sorted(pending)}")
    for record in records:
        _status, content, _headers = smoke_test.call(
            "GET",
            f"/artifacts/{record['artifact_id']}/download",
            token=token,
        )
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise RuntimeError(f"Artifact checksum changed for {record['artifact_id']}")
    health = smoke_test.json_call("GET", "/system/health", token=token)
    if health.get("database") != "ok" or health.get("storage") != "ok":
        raise RuntimeError(f"System health failed after SQLite soak: {health}")
    return {
        "status": "ok",
        "project_id": state["project_id"],
        "run_count": len(records),
        "database": health["database"],
        "storage": health["storage"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("enqueue", "verify", "full"))
    parser.add_argument("state", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--runs", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.workers < 1 or args.runs < 1:
        parser.error("workers and runs must be positive")
    if args.phase in {"enqueue", "full"}:
        result = enqueue(args.state, min(args.workers, args.runs), args.runs)
        print(json.dumps({"status": "enqueued", "run_count": len(result["runs"])}))
    if args.phase in {"verify", "full"}:
        print(json.dumps(verify(args.state, args.timeout), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as soak_error:
        print(f"SQLite soak failed: {soak_error}", file=sys.stderr)
        raise SystemExit(1)
