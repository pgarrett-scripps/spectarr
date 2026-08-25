# Spectarr conversion adapter

This service turns a typed Spectarr job into one ProteoWizard invocation. It uses `msconvert-cli` for Docker command construction, then adds the safety and result contract required by Spectarr.

Key properties:

- One source acquisition per job
- Allowlisted and versioned recipes only
- Pinned ProteoWizard image
- Read-only input mounts
- A new scratch directory for every job
- Output discovery, format validation, checksums, logs, and timings
- Structured JSON result on standard output
- Exact profile snapshots recorded with every output
- MSCLI packaged named configs copied into shared per-job scratch before Docker execution

Install the sibling package and this package into the worker environment:

```bash
python -m pip install -e ../msconvert-cli -e services/converter
```

Run one job:

```bash
spectarr-convert request.json \
  --source-root /srv/spectarr/library \
  --scratch-root /srv/spectarr/scratch
```

The backend must promote validated outputs from scratch into managed artifact storage. A successful result does not modify or delete the source.

For continuous API-backed execution, run:

```bash
spectarr-converter-worker
```

The worker polls queued conversion jobs, claims one atomically, resolves the source through the artifact location endpoint, converts it, streams the output back to the artifact upload endpoint, and completes the job. The API and worker must share the managed storage volume for directory-based vendor sources.

Set `SPECTARR_WORKER_TOKEN` to the same value configured by the API. The worker sends it through the dedicated `X-Spectarr-Worker-Token` header.

When the worker uses the host Docker socket, configure path translation so bind mounts refer to host paths:

```text
SPECTARR_CONTAINER_DATA_ROOT=/data
SPECTARR_DOCKER_DATA_ROOT=/absolute/host/path/to/spectarr/data
```

The converter image expects a BuildKit named context called `msconvert_cli`. Compose should map that context to the sibling `../msconvert-cli` checkout. This makes the worker use the local package rather than an unrelated registry build.
