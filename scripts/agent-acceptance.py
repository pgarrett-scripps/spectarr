#!/usr/bin/env python3
"""Exercise agent discovery over MCP and verify explicitly mapped local files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

import smoke_test as api


def mcp(method: str, params: dict) -> dict:
    response = api.external_json_call(api.MCP_URL, {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    })
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def tool(name: str, **arguments):
    result = mcp("tools/call", {"name": name, "arguments": arguments})
    if result.get("isError"):
        raise RuntimeError(result)
    return json.loads(result["content"][0]["text"])


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-results", required=True, type=Path)
    parser.add_argument("--library-root", required=True, type=Path,
                        help="Explicit host mapping for the API server library root")
    parser.add_argument("--rename", action="store_true", help="Rename only the acceptance project")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    vendor = json.loads(args.vendor_results.read_text())
    project_id = vendor["project_id"]
    api.wait_until_ready()
    token = api.authenticate()
    initialized = api.wait_for_external_json(api.MCP_URL, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })["result"]
    assert "API server" in initialized["instructions"]
    assert all(row["annotations"]["readOnlyHint"] for row in mcp("tools/list", {})["tools"])
    projects = tool("list_projects")
    assert any(project["id"] == project_id for project in projects)
    assert tool("list_experiments", project_id=project_id)
    runs = []
    offset = 0
    while offset is not None:
        page = tool("search_runs", project_id=project_id, limit=1, offset=offset)
        runs.extend(page["items"])
        offset = page["next_offset"]
    assert len(runs) == page["total"]
    assert len({run["id"] for run in runs}) == len(runs)
    expected_runs = {row["run_id"] for row in vendor["fixtures"].values()}
    if vendor.get("open_source"):
        expected_runs.add(vendor["open_source"]["run_id"])
    assert expected_runs <= {run["id"] for run in runs}
    api.wait_for("idle acceptance processing", lambda: api.json_call("GET", "/jobs", token=token),
                 lambda jobs: not any(job["state"] in {"queued", "running"} for job in jobs), 1200)
    before = {}
    for run in runs:
        for artifact in tool("list_run_artifacts", run_id=run["id"]):
            before[artifact["id"]] = tool("resolve_artifact", artifact_id=artifact["id"])["server_path"]
    if args.rename:
        project = api.json_call("GET", f"/projects/{project_id}", token=token)
        assert project["name"].startswith("Release acceptance ")
        api.json_call("PATCH", f"/projects/{project_id}", {"name": project["name"] + " renamed"}, token)
        api.json_call("POST", "/library/rebuild", {}, token)
    results = []
    library_root = args.library_root.resolve()
    for run in runs:
        current = tool("get_run", run_id=run["id"])
        manifest = api.json_call("GET", f"/runs/{run['id']}/manifest", token=token)["run"]
        assert manifest["summary_basis"] == current["summary_basis"]
        assert manifest["metadata"].get("spectra_count") == current["spectraCount"]
        artifacts = tool("list_run_artifacts", run_id=run["id"])
        source_ids = {artifact["id"] for artifact in artifacts if artifact["role"] == "source"}
        for artifact in artifacts:
            access = tool("resolve_artifact", artifact_id=artifact["id"])
            assert access["path_scope"] == "api_server"
            assert access["integrity"] == "checksum_recorded_not_reverified"
            assert access["role"] == artifact["role"]
            assert access["run_id"] == run["id"]
            if artifact["state"] != "ready":
                assert access["server_path"] is None
                continue
            assert access["availability"] == "available"
            relative = PurePosixPath(access["library_relative_path"])
            assert not relative.is_absolute() and ".." not in relative.parts
            path = (library_root / str(relative)).resolve()
            assert path.is_relative_to(library_root)
            assert str(PurePosixPath(access["library_root"]) / relative) == access["server_path"]
            if args.rename:
                assert access["server_path"] != before[artifact["id"]]
            if artifact["role"] == "derived":
                assert access["parent_artifact_id"] in source_ids
                assert access["recipe_id"] and access["recipe_fingerprint"]
            if access["is_directory"]:
                assert access["download_url"] is None and path.is_dir()
                bundle = artifact["bundle_manifest"]
                canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
                assert hashlib.sha256(canonical).hexdigest() == access["sha256"]
                for member in bundle["files"]:
                    member_path = (path / member["path"]).resolve()
                    assert member_path.is_relative_to(path)
                    assert digest(member_path) == member["sha256"]
            else:
                assert digest(path) == access["sha256"]
                _, downloaded, _ = api.call("GET", f"/artifacts/{artifact['id']}/download", token=token, timeout=180)
                assert hashlib.sha256(downloaded).hexdigest() == access["sha256"]
            for query in (artifact["id"], artifact["original_filename"], "sha256:" + artifact["sha256"], access["library_relative_path"]):
                matches = tool("search_runs", project_id=project_id, query=query)
                assert run["id"] in {match["id"] for match in matches["items"]}
            results.append({"artifact_id": artifact["id"], "run_id": run["id"],
                            "role": access["role"], "sha256": access["sha256"],
                            "directory": access["is_directory"], "verified": True})
    if vendor.get("open_source"):
        matches = tool("search_runs", query=vendor["open_source"]["sha256"], project_id=project_id)
        assert matches["total"] == 2
    result = {"status": "ok", "run_count": len(runs), "artifacts": results,
              "renamed_and_rebuilt": args.rename, "pagination_limit": 1,
              "duplicate_content_keeps_distinct_runs": bool(vendor.get("open_source"))}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "artifacts"}))


if __name__ == "__main__":
    main()
