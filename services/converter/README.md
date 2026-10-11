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
- Final mzML outputs rewritten as pyMZML-compatible self-indexed `.mzML.gz` artifacts through Spxtacular and mzMLPy

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

For mzML recipes, ProteoWizard first produces a temporary `.mzML`. The service validates it,
creates a self-indexed `.mzML.gz`, validates standard gzip decompression, and removes only the
temporary generated mzML before upload. Source acquisitions remain read-only.

The development Compose build installs mzMLPy and Spxtacular from their published main
branches. Override `SPECTARR_MZMLPY_BUILD_CONTEXT` or
`SPECTARR_SPXTACULAR_BUILD_CONTEXT` to test a different Git ref or local BuildKit context.

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

## Local native conversion

The Rust backend is opt-in for each recipe. ProteoWizard remains the default, and existing presets keep their existing backend. Set `SPECTARR_MSCONVERT_RS_BINARY` to an absolute path visible to the worker, then create a recipe with `converter: "msconvert-rs"`. The native binary is not added to the production image automatically.

For a Docker installation, use the optional `compose.native.yaml` from the release bundle. Set `SPECTARR_MSCONVERT_RS_BINARY` in your environment file to the absolute host path of a Linux x86-64 executable compatible with Debian Trixie, then run `docker compose -f compose.yaml -f compose.native.yaml up -d`. The override mounts the executable read-only and supplies its container path to the worker. For a source checkout, use `-f release/compose.native.yaml` instead. Verify the mounted executable with `docker compose -f compose.yaml -f compose.native.yaml exec spectarr /opt/native/msconvert-rs capabilities --json` before submitting a native job.

Native recipes default to 64-bit m/z and intensity arrays. An explicit 32-bit request is preserved. Unsupported native formats, presets, peak-picking filters and compression are rejected when creating or updating the recipe. Existing ProteoWizard precision defaults remain unchanged.

The dashboard can select native profiles for processing and enable or disable them. Configure their settings through the API. The ProteoWizard profile editor is disabled for native profiles so it cannot discard native preservation policies or resource limits.

Example API recipe:

```json
{
  "name": "Native stored Thermo centroids",
  "converter": "msconvert-rs",
  "converter_version": "0.1.0",
  "output_format": "MGF",
  "parameters": {
    "stored_centroids": true,
    "require_retention_time": true,
    "timeout_seconds": 300,
    "max_peaks_per_spectrum": 2000000,
    "filters": [{"kind": "ms_level", "levels": [2]}]
  }
}
```

Every native job checks the executable's advertised outputs and version, runs a dry-run preflight, then verifies the native output hash. Native JSON reports, the complete dry-run preflight plan, capability details, the executable SHA-256 and the immutable recipe snapshot become artifact provenance. The native report describes the intermediate output before Spectarr wraps mzML in indexed gzip. The artifact record contains the final indexed-gzip checksum.

Cancellation stops the native process group, and structured progress updates the existing job progress indicator. A content-addressed source is given its scientific filename through a temporary hardlink or copy. Source files are never renamed or edited.

Native mzML, MGF and MS2 are supported. Bruker experimental consent, stored Thermo centroids, retention-time and mobility requirements, and bounded memory, temporary storage, decompression, peak count and timeout policies are typed recipe fields. Native mzML supports explicit 32-bit or 64-bit arrays and zlib or uncompressed arrays. An optional integer `compression_level` from 0 through 9 controls native mzML zlib encoding. Level 0 is retained explicitly. This field is rejected for ProteoWizard recipes, non-mzML outputs and uncompressed arrays. It applies to the native intermediate mzML, not the later indexed gzip packaging. Typed filter integration currently supports MS levels and most-intense absolute/count thresholds.

Native recipes can enforce preservation requirements through `parameters.loss_policy`:

```json
{
  "intensity_precision": 64,
  "loss_policy": {
    "deny": ["unqualified-calibration", "precision-reduction"],
    "require": ["polarity", "precursor-target", "collision-energy"]
  }
}
```

Each denial becomes `--forbid-loss` and each required field becomes `--require-field` in both preflight and conversion. Supported denials are `unqualified-calibration`, `precision-reduction`, `mobility-collapse`, `chromatogram-loss`, `reduced-text-metadata` and `experimental-processing`. Supported required fields are `polarity`, `precursor-isolation`, `precursor-target`, `collision-energy` and `precursor-charge`. A precursor target does not imply known isolation widths. The native converter evaluates these requirements against the source, recipe and selected spectra and fails before publishing output when they cannot be met.

Unknown policy fields, names and value types are rejected. Nonempty policies require an explicit `msconvert-rs` recipe. An empty policy keeps existing ProteoWizard recipes compatible. The API retains policies in immutable job snapshots and validates updates using the stored converter and updated parameters.

`allow_approximate_calibration` is a separate strict boolean, false by default, for explicit investigation of unsupported Bruker calibration layouts. It requires `allow_experimental: true` and the native backend. It is passed as `--allow-approximate-calibration` and does not disable preservation requirements. A recipe that also denies unqualified calibration remains subject to that denial. These controls do not change the default converter or deploy a new native binary.

MS1 remains available through the standalone Rust CLI. Spectarr MS1 artifact validation and UI integration are not enabled.

ProteoWizard vendor peak picking, named presets, mzXML and Numpress fail explicitly on the native backend. No fallback changes the requested converter or centroiding algorithm. Experimental native centroiding and the broader native filter grammar remain available directly through the CLI, pending matching typed API filters.

Run the local integration checks against a built binary:

```bash
PYTHONPATH=services/converter/src \
SPECTARR_MSCONVERT_RS_TEST_BINARY=/absolute/path/to/msconvert-rs \
services/.venv/bin/python -m unittest discover -s services/converter/tests
```
