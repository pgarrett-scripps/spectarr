# Conversion and MCP integration

Set `SPECTARR_MCP_PUBLIC_URL` to the endpoint clients should use when publishing MCP on a custom port or behind a reverse proxy. For example, `http://localhost:8341/mcp` or `https://spectarr.example/agent/mcp`. The API exposes this nonsecret value in system health, and the dashboard displays and copies it. Without an explicit value, the dashboard labels its same-host port 8281 suggestion as the default endpoint.

## Conversion ownership

MassSpec owns job state, artifact records, storage promotion, retention, and provenance. The converter service owns one ProteoWizard invocation and returns a structured result. `msconvert-cli` remains the low-level Docker command builder.

The backend should execute conversion jobs in this order:

1. Resolve the source artifact to an allowed local path.
2. send a typed request with a backend-generated job ID and recipe name.
3. Receive the structured result.
4. Verify the reported checksum while promoting the file into managed artifact storage.
5. Create the derived artifact and provenance records in one database transaction.
6. Delete scratch data only after promotion succeeds.

The source mount is read-only. A failed conversion remains isolated in its job scratch directory. Only validated outputs appear in a successful result.

Content-addressed source files may not retain their original extension on disk. The worker bind-mounts those files under the recorded original filename inside the container so ProteoWizard can still identify the vendor format. Directory bundles retain their original root name.

## Current recipes

- `archival-mzml-v1` produces a 64-bit, zlib-compressed mzML.
- `search-mgf-v1` produces centroided MS2 spectra in MGF.
- `search-ms2-v1` produces centroided tandem spectra in MS2.

Recipes are versioned because filter changes can alter scientific results. The service does not accept arbitrary command arguments.

## msconvert-cli library contract

MassSpec requires `msconvert-cli` 1.2 or newer. That release provides:

- A public structured result containing outputs, checksums, logs, duration, command metadata, and tool version
- A public command builder and single-job library method
- Native read-only input and configuration mounts
- A pinned default image
- Atomic discovery of `.d` and other directory-based vendor bundles
- Output validation hooks
- Cancellation events and phase progress callbacks

MassSpec adds format-specific scientific validation before promoting any output into managed storage. The upstream package owns Docker command construction and process metadata. This keeps MassSpec off private package methods and makes the integration independently testable.

MassSpec should keep one layer of concurrency. The job worker schedules individual conversion jobs, and msconvert-cli should run one acquisition per call.

## MCP boundary

The MCP server calls `/api/v1` and never reads SQLite or artifact paths. Read resources cover projects, runs, artifacts, and jobs. Safe tools search runs and inspect state. Write tools queue derivatives, add annotations, or retry jobs.

Writes are disabled by default. Enabling them still requires a scoped API key and confirmation on each tool call. This boundary is also the integration model for a future Searcharr service or any client written in another language.
