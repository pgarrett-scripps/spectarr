# Acceptance and release preparation, September 25, 2026

This pass covers real acquisitions, agent file discovery, simpler processing controls, and a reproducible local release candidate. All destructive lifecycle tests run in disposable installations with read-only fixture mounts. The live installation remains unchanged. Automatic approval review blocked its replacement, so deployment awaits explicit approval.

## Scientific acceptance

| Fixture | Independent check | Expected observation |
| --- | --- | --- |
| Thermo angiotensin RAW | Count mzML spectrum elements and MS-level CV terms | 10 MS2 records in both source summary and converted mzML |
| Larger vendor RAW | Count converted mzML elements after direct-reader failure | 5,445 spectra, including 2,000 MS2 records. The run explicitly identifies converted fallback observations |
| Bruker directory bundle | Read the vendor SQLite database and independently count converted mzML elements | 65 MS1 frames plus 4,633 PASEF entries match 4,698 source records. Converted mzML contains 476,410 spectra |
| Reimported Thermo mzML | Upload converted bytes as a separate source and extract again | 10 spectra. Equal content hashes retain two distinct runs and source versus derivative roles |

The Bruker database contains 710 frames in total. The direct reader's count is not the frame count and is not interchangeable with the converted spectrum count. The converted file contains 43,615 MS1 and 432,795 MS2 spectra. Both counts are retained with their artifact provenance.

The vendor runner also verifies Thermo conversion to mzML, mzXML, MGF and MS2, authenticated download hashes, cancellation and retry, derivative reclamation, and byte-identical MGF regeneration. XML validation uses Python's XML parser independently of the extraction providers. Bruker validation reads the acquisition database in read-only mode.

These fixtures establish workflow correctness for the tested files. They do not establish quantitative equivalence across readers or all vendor formats. The earlier HYE identification experiment remains historical evidence and was not repeated in this pass.

## Agent acceptance

The MCP runner discovers projects and experiments, follows run pagination one result at a time, distinguishes source artifacts from derivatives, resolves recorded identity and availability, and opens files through an explicit host mount mapping. It independently hashes files, downloads single-file artifacts, and checks every vendor-bundle member against its manifest.

It repeats filename, stable ID, full checksum and managed-path searches after a project rename and a library rebuild. A further pass verifies the same IDs, content and provenance after restarting the application. Matching content in the imported mzML and the original conversion must produce distinct run matches.

The existing permission regression tests also verify that a read-scoped client cannot discover or resolve another project's artifacts. The HTTP MCP adapter remains intended for trusted loopback access. Incoming caller authentication for a shared or remote MCP endpoint remains separate work.

## Processing interface

The run page presents mzML and MGF first, with mzXML and MS2 under Other formats. It omits the source's existing format, disables processing when the source is unavailable, identifies the input file, lists available outputs, and shows active or failed jobs with progress or error details. Finished activity is collapsed and each job identifies its input file. Matching outputs are reused by the backend.

Desktop and 390-pixel mobile checks verify the disclosures, file links, output reuse, readable wrapping, and absence of page overflow. Regression tests cover unavailable sources, job state, route changes and background refresh.

## Reliability finding and fix

The first expanded rehearsal exposed an idle converter failure during library publication. The API correctly returned HTTP 503 while its publication journal existed, but the converter treated that response as fatal. The supervisor restarted the container and interrupted MCP requests.

The converter now retries explicit transient API responses, including HTTP 503, with bounded backoff. Authentication and other permanent errors still fail visibly. The new container acceptance check holds a real publication journal and exclusive maintenance lock for eight seconds. It requires the converter process to stay alive and API reads to recover afterwards.

The backup helper now runs as the container's configured service UID and GID. This avoids creating maintenance lock files under a different owner.

## Reproduction

Use an exact local image ID for the complete rehearsal. The fixture directory must contain the three previously authorized inputs shown here.

```bash
SPECTARR_IMAGE_REF=sha256:55a66bb25d4965c6db735fa41e7e9799755879523594c566a3b3b89fe83c6d87 \
SPECTARR_REHEARSAL_PULL=false \
SPECTARR_KEEP_REHEARSAL=true \
SPECTARR_ACCEPTANCE_IMPORT_DIR=/home/ty/Repos/spectarr/imports/release-acceptance \
scripts/release-rehearsal.sh
```

The retained temporary directory contains vendor, MCP and scientific JSON reports, container logs, an isolated database and storage, backup archives, and the restored installation. Its local test password is stored with mode 0600 for diagnostic resumption. Do not include that password file or runtime secrets in published reports.

The complete fixture matrix can take longer than ten minutes when conversions and indexing share the worker. The metadata wait budget is twenty minutes. A retained run can be resumed with `SPECTARR_REHEARSAL_RESUME_DIR` and `SPECTARR_ACCEPTANCE_PROJECT_ID`. The vendor runner reuses the acceptance project's existing imports and matching conversions. The rehearsal's thirty-second job lease now reaches the container through Compose, while normal installations retain the existing five-minute default.

`DEPENDENCIES.env` now pins the mzmlpy and Spxtacular revisions used in the image. CI applies `release/dependency-patches/msconvert-cli-sage.patch` to the pinned converter checkout. This captures the previously local correction that centroids every MS level in the Sage preset.

To capture exact working sources, including uncommitted dependency changes:

```bash
python3 scripts/capture-candidate.py storage/release-candidates/2026-09-25/sources \
  --image sha256:55a66bb25d4965c6db735fa41e7e9799755879523594c566a3b3b89fe83c6d87
```

Verify SHA256SUMS, then extract the four source archives into one parent directory. The Spectarr Compose build resolves the three sibling dependency directories automatically. Source manifests record the original commits, local changes, file hashes and executable modes. The source capture excludes ignored data, credentials, installed packages and Git internals. This supports reproducing the tested source inputs without claiming bit-identical Docker layers across different build times.

## Component verification

- Backend: 189 tests passed, with 88.71 percent coverage. Ruff and configured mypy checks passed.
- Frontend: 127 tests passed, TypeScript checks, ESLint and production build passed.
- Services: 139 tests passed, including 106 tests inside the final Docker image and 33 acquisition-agent tests on the host.
- Version consistency, dependency constraints, workflow YAML parsing, shell syntax and Git whitespace checks passed.
- The eight-second maintenance window returned HTTP 503 as expected, preserved the converter process, then returned HTTP 200 after recovery.

One frontend run under simultaneous image builds and vendor processing exceeded a one-second async assertion deadline. The complete suite passed with four workers, without changing the test or product code.

## Release scope

This is a local candidate based on version 0.3.0 with unreleased changes. The local review branch `codex/acceptance-20260925` records the completed project changes without switching the current checkout or staging its working changes. No release tag or registry image is published. A public release still requires a new version, the hosted Linux and Windows release gates, and promotion of the tested registry digest. The current source snapshot and locally tested image provide a reviewable candidate without altering the existing working changes.

## Final rehearsal result

The final image passed the complete real-file matrix, MCP discovery before and after restart, independent scientific checks, and the maintenance recovery gate. All 24 concurrent imports survived restart with valid database and storage checks. The coordinated backup verified 13 artifact objects, and a separate restored instance started and validated the same objects. Its snapshot was 1,104,343,040 bytes.

Machine-readable results are recorded in [acceptance-2026-09-25.json](acceptance-2026-09-25.json). The local delivery package is under `storage/release-candidates/2026-09-25`, with the exact image export, installation bundle, source archives and checksums. A separate test-data preview remains available at http://localhost:3291/. The live instance at http://localhost:3280/ was not replaced.
