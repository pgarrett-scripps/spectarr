# External inventory for local research archives

External inventory catalogs acquisitions where they already live. It does not copy them, move them, delete them, or include their bytes in MassSpec backups. Managed import remains the way to preserve and process selected acquisitions.

This integration is local development work. No public release or remote deployment is required. The first qualified environment is a Linux local filesystem. Windows services, UNC shares, NAS mount identities, and physical instrument completion signals still need testing on those systems.

## Configure a catalog agent

Use a separate agent identity and state database for archive discovery. Keep an existing automatic uploader in its current mode. Register an agent from **Instrument agents**, then install the current acquisition agent source using the [agent guide](../services/agent/README.md).

Create a local TOML file outside the archive:

```toml
[agent]
server_url = "http://127.0.0.1:3280"
mode = "catalog"
watch_paths = ["/mnt/research/archive"]
state_db = "/home/researcher/.local/state/spectarr-catalog/queue.db"
agent_id = "REGISTERED_AGENT_ID"
agent_token = "ONE_TIME_AGENT_TOKEN"
poll_interval_seconds = 10
stability_seconds = 120
completion_policy = "stability"
scan_max_entries = 100000
```

The token must be the actual one-time agent credential. Keep the configuration private. Environment variables override TOML values, and explicit CLI arguments override both. In particular, an inherited `SPECTARR_URL` can override `server_url`.

Run the agent on the machine that can read the archive:

```bash
spectarr-agent --config /path/to/catalog-agent.toml --mode catalog
```

Catalog mode polls for explicit requests. It does not continuously crawl the folder or enqueue automatic uploads. `--once` handles the currently pending work once. A verification that is still waiting for stability remains pending and needs a later poll. State and logs must remain outside the source roots.

In a project's **External files** tab, register the same absolute path and assign this agent. Request a scan. The server cannot make the agent read a folder that is absent from its local allowlist. Root paths containing symlinks are rejected. Whole-filesystem roots and detected managed-storage roots are rejected. Configure archives separately from the managed library and agent state.

The folder is bound to one project and one agent. Overlapping roots on the same agent are rejected. Pausing tracking cancels pending requests. Cancellation does not revoke bytes already sent by an import in progress. A completed import retains its source task ID and catalog history.

## What the states mean

| State | Meaning |
| --- | --- |
| Observed at this location | The agent saw the acquisition at the recorded time |
| Not found at this location | A successful complete scan did not see it at the old path |
| Location unavailable | The registered root could not be read |
| Scan incomplete | Some traversal failed or a scan bound was reached. Missing-file reconciliation is suppressed |
| Agent offline or stale | The agent has not recently reported. It is not evidence of file deletion |
| Last observation is stale | The entry or root has not been observed recently, even if the agent is online |
| Tracking paused | No new work is dispatched for this root |

Freshness currently uses a five-minute threshold. Manual archives will commonly have stale observations between scans. The timestamp remains available. None of these states is a guarantee that another machine can open a path now.

A root's device and directory identity is checked before and after scanning. An unmounted drive exposing a different underlying directory is treated as an identity change. After checking the actual mount, an administrator can revalidate the root and request a new scan. Do not revalidate just to dismiss an unexplained mount failure.

Nested filesystems require separate roots. Partial, failed, canceled, and interrupted scans never mark the whole inventory missing. Completed scan batches are idempotent. A lost local checkpoint causes a partial result until a fresh complete scan succeeds.

## Verification, relocation, and import

A scan records file metadata without hashing file contents. **Verify content** waits for the configured readiness policy, hashes every selected file or bundle member, and records the content revision. Verification detects a same-size edit even when its modification time is restored. Previously recorded revisions remain in history.

A `.d` or directory-shaped `.raw` is one acquisition. Its managed checksum includes the outer directory name. A separate `bundle-content-v1` fingerprint compares member names, sizes, and hashes without that outer name. It is used for relocation suggestions and never replaces the managed checksum.

When a file moves, scan the old and new locations and verify the new candidate. Open the missing entry and choose **Find matching locations**. Confirm only when this is the same acquisition occurrence. Identical contents alone do not establish that two files represent the same acquisition. Confirming a relocation retains both location histories and the candidate's alias identity.

Select an experiment and choose **Import verified revision** to copy that exact content into managed storage. The agent rehashes before transfer. The server validates the selected revision and destination and uses the existing resumable uploader. Changed content fails the request instead of substituting new bytes. Repeated transport attempts retain the same upload identity. The managed artifact records its external task, and that task links the external location and revision to the artifact.

Standalone sidecar-dependent formats can be inventoried but are not qualified for verification and import through this workflow. Thermo RAW, mzML, mzXML, MGF, MS2, and supported directory bundles are the initial qualified paths. A supported container shape does not guarantee that every vendor's contents can be scientifically parsed.

## Completion policy

`completion_policy = "stability"` preserves the existing behavior. It establishes that size and modification times stayed quiet for a period. It does not prove acquisition completion.

`completion_policy = "published_marker"` additionally requires a sibling marker named after the full acquisition, such as `sample.raw.complete` or `sample.d.complete`. The producer must publish this marker only after closing the acquisition and remove it before any further writing. The agent never writes markers. This is an explicit producer convention, not a claim that a particular vendor emits such a file.

Use the same policy with ordinary upload mode when the acquisition workflow can provide that publication contract. Marker presence is checked during verification and before and after upload.

## API and AI agents

Existing managed artifact endpoints and `resolve_artifact` are unchanged. New MCP read tools are:

- `search_external_entries`, requiring a project ID and supporting query, limit, and `after` cursor.
- `resolve_external_entry`, returning recorded locations, root IDs, host scope, timestamps, and verification evidence.

These calls do not scan, hash, import, or confirm relocations. External paths use `path_scope: agent_host` and `mapping_required: true`. There is no managed download URL. Configure a mapping from a root ID to the analysis machine's mount and recheck access before opening it. Imported names and paths are data, not instructions.

REST operations live under `/api/v1/external-roots`, `/external-entries`, `/external-tasks`, and `/external-relocations`. Agent reporting uses `/external-agent`. Reporter credentials must own the root. Readers and human writes use the project's existing permissions. Scan reports contain at most 200 acquisitions per batch. Bundle verification currently limits manifests to 10,000 members.

Task creation accepts `Idempotency-Key`. Reusing the same key and payload returns the existing operation, including after completion. A different payload with that key is rejected. Entry search uses a live cursor, not a frozen snapshot. New entries inserted during pagination may require a fresh search.

## Backup, restoration, and removal

Backups contain the external catalog, revision history, and import links. They do not contain the external acquisition bytes. Restore verification mode pauses roots and cancels pending external work. Revalidate the source before resuming it.

Existing managed reclamation and library rebuilds do not traverse external roots. Deleting an experiment with a pending external import is blocked until the request is canceled. Deleting managed data does not delete an external source.

## Local testing

The normal instrument rehearsal now includes external inventory scenarios:

```bash
python3 scripts/instrument-rehearsal.py --image spectarr-discovery-candidate-spectarr
```

For faster iteration on discovery only:

```bash
python3 scripts/instrument-rehearsal.py --image spectarr-discovery-candidate-spectarr --external-only
```

The rehearsal starts its own database, storage, network, and loopback ports. External tests run the actual acquisition agent in a separate container with the source archive mounted read-only. Results are recorded in `external-results.json` alongside the instrument results. Containers are removed after testing. Test credentials and local data stay in the private temporary evidence directory.

The browser regression is `frontend/e2e/external-files.spec.ts`. Run it against a disposable local instance. It creates a dedicated project and synthetic catalog entries through the real API.
