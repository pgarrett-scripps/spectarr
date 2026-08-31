#!/usr/bin/env python3
"""Run the authorized real-file release acceptance matrix against Spectarr."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import PurePosixPath

import smoke_test


def wait_job(job_id: str, token: str | None, timeout: int = 1200) -> dict[str, object]:
    job = smoke_test.wait_for(
        f"job {job_id}",
        lambda: smoke_test.json_call("GET", f"/jobs/{job_id}", token=token),
        lambda value: value.get("state") in {"succeeded", "failed", "cancelled"},
        timeout,
    )
    if job.get("state") != "succeeded":
        raise RuntimeError(f"Job did not succeed: {job}")
    return job


def create_imported_run(
    project_id: str,
    label: str,
    source_path: str,
    token: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    experiment = smoke_test.json_call(
        "POST",
        "/experiments",
        {"project_id": project_id, "name": label},
        token,
    )
    sample = smoke_test.json_call(
        "POST",
        "/samples",
        {"experiment_id": experiment["id"], "name": f"{label} sample"},
        token,
    )
    run = smoke_test.json_call(
        "POST",
        "/runs",
        {
            "experiment_id": experiment["id"],
            "sample_id": sample["id"],
            "name": label,
            "source_class": "vendor",
        },
        token,
    )
    artifact = smoke_test.json_call(
        "POST",
        f"/runs/{run['id']}/artifacts/import",
        {"source_path": source_path, "role": "source"},
        token,
    )
    return run, artifact


def convert_format(run_id: str, output_format: str, token: str | None) -> dict[str, object]:
    job = smoke_test.json_call(
        "POST",
        f"/runs/{run_id}/derivatives",
        {"format": output_format},
        token,
    )
    job = wait_job(str(job["id"]), token)
    output_id = job.get("output_artifact_id")
    if not output_id:
        raise RuntimeError(f"Conversion did not produce an artifact: {job}")
    artifact = smoke_test.json_call("GET", f"/artifacts/{output_id}", token=token)
    _status, content, _headers = smoke_test.call(
        "GET",
        f"/artifacts/{output_id}/download",
        token=token,
        timeout=180,
    )
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != artifact.get("sha256"):
        raise RuntimeError(f"Downloaded checksum failed for {output_id}")
    return {
        "job_id": str(job["id"]),
        "artifact_id": str(output_id),
        "sha256": checksum,
        "byte_size": len(content),
    }


def cancel_and_retry(
    run_id: str,
    recipe_id: str,
    token: str | None,
) -> dict[str, object]:
    batch = smoke_test.json_call(
        "POST",
        "/processing-batches",
        {
            "scope_type": "runs",
            "scope_ids": [run_id],
            "recipe_ids": [recipe_id],
            "mode": "force",
            "label": "Vendor acceptance cancellation",
        },
        token,
    )
    batch_id = str(batch["id"])
    running = smoke_test.wait_for(
        "running conversion for cancellation",
        lambda: smoke_test.json_call("GET", f"/processing-batches/{batch_id}", token=token),
        lambda value: value.get("running_count", 0) > 0,
        1200,
    )
    job_id = str(running["items"][0]["job_id"])
    cancelled = smoke_test.json_call(
        "POST",
        f"/processing-batches/{batch_id}/cancel",
        {},
        token,
    )
    if cancelled.get("cancelled_count") != 1:
        raise RuntimeError(f"Running batch was not cancelled: {cancelled}")
    retried = smoke_test.json_call(
        "POST",
        f"/processing-batches/{batch_id}/retry",
        {},
        token,
    )
    retried_job_id = str(retried["items"][0]["job_id"])
    completed = wait_job(retried_job_id, token)
    return {
        "batch_id": batch_id,
        "cancelled_job_id": job_id,
        "retried_job_id": retried_job_id,
        "output_artifact_id": str(completed["output_artifact_id"]),
    }


def extraction_summary(run_id: str, token: str | None) -> dict[str, object]:
    run = smoke_test.wait_for(
        f"metadata extraction for {run_id}",
        lambda: smoke_test.json_call("GET", f"/runs/{run_id}", token=token),
        lambda value: bool(value.get("latest_extraction")),
        600,
    )
    extraction = run["latest_extraction"]
    return {
        "provider": extraction.get("extractor"),
        "spectra_count": run.get("spectraCount"),
        "warnings": extraction.get("warnings", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thermo", required=True, help="Allowed import path for a small Thermo RAW")
    parser.add_argument("--large-raw", required=True, help="Allowed import path for a larger vendor RAW")
    parser.add_argument("--bruker", required=True, help="Allowed import path for an atomic Bruker .d directory")
    parser.add_argument("--output", help="Write the JSON result to this path")
    args = parser.parse_args()

    smoke_test.wait_until_ready()
    token = smoke_test.authenticate()
    suffix = uuid.uuid4().hex[:8]
    project = smoke_test.json_call(
        "POST",
        "/projects",
        {"name": f"Release acceptance {suffix}"},
        token,
    )
    fixtures = {
        "thermo": args.thermo,
        "large_raw": args.large_raw,
        "bruker": args.bruker,
    }
    runs: dict[str, dict[str, object]] = {}
    sources: dict[str, dict[str, object]] = {}
    for key, source_path in fixtures.items():
        label = PurePosixPath(source_path).name
        run, artifact = create_imported_run(str(project["id"]), label, source_path, token)
        runs[key] = run
        sources[key] = artifact

    summaries = {key: extraction_summary(str(run["id"]), token) for key, run in runs.items()}
    thermo_outputs = {
        output_format: convert_format(str(runs["thermo"]["id"]), output_format, token)
        for output_format in ("mzML", "mzXML", "MGF", "MS2")
    }

    recipes = smoke_test.json_call("GET", "/recipes", token=token)
    mzml_recipe = next(recipe for recipe in recipes if recipe.get("output_format") == "mzML")
    cancellation = cancel_and_retry(str(runs["large_raw"]["id"]), str(mzml_recipe["id"]), token)

    purge_payload = {
        "scope_type": "runs",
        "scope_ids": [str(runs["thermo"]["id"])],
        "formats": ["MGF"],
    }
    reclaim_preview = smoke_test.json_call("POST", "/storage/reclaim/preview", purge_payload, token)
    if reclaim_preview.get("artifact_count", 0) < 1:
        raise RuntimeError(f"No MGF derivative was reclaimable: {reclaim_preview}")
    reclaimed = smoke_test.json_call(
        "POST",
        "/storage/reclaim",
        {**purge_payload, "confirmation": "PURGE DERIVED FILES"},
        token,
    )
    regenerated = convert_format(str(runs["thermo"]["id"]), "MGF", token)

    result = {
        "status": "ok",
        "project_id": str(project["id"]),
        "fixtures": {
            key: {
                "path": fixtures[key],
                "run_id": str(runs[key]["id"]),
                "source_artifact_id": str(sources[key]["id"]),
                "source_sha256": sources[key]["sha256"],
                "source_bytes": sources[key]["byte_size"],
                "extraction": summaries[key],
            }
            for key in fixtures
        },
        "thermo_outputs": thermo_outputs,
        "cancellation": cancellation,
        "reclamation": {
            "preview": reclaim_preview,
            "result": reclaimed,
            "regenerated": regenerated,
        },
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as acceptance_error:
        print(f"Vendor acceptance failed: {acceptance_error}", file=sys.stderr)
        raise SystemExit(1)
