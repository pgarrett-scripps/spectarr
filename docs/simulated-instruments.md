# Simulated instrument upload testing

The test system replays acquisition writes through the production acquisition agent and a real, isolated MassSpec Docker instance. It tests file lifecycle and transport behavior. It does not simulate instrument electronics, vendor control software or physical acquisition timing.

## Run the portable gate

Build or select an existing local MassSpec image, then run from the repository:

```bash
python3 scripts/instrument-rehearsal.py --image spectarr-spectarr
```

The runner resolves the image to its immutable ID. It allocates separate loopback ports and creates a fresh temporary data directory. It refuses a nonempty catalog. No live data directories or instrument folders are mounted into the test server. The agent reads files written by the test producer in a separate temporary folder.

The portable gate generates 1,200 MGF spectra from the repository fixture. Run it on Linux with Docker Compose, a local image and Python 3.11 or newer. It does not qualify Windows service behavior or network-share semantics. The runner uses Python's standard library and executes the current agent source in a subprocess. The tested server image and agent source are therefore separate inputs.

Linux integration CI invokes the portable gate against its freshly built image. CI retains only the JSON results, image identity and failure summary. It does not upload the agent configuration or queue database, which contain test credentials.

## Include native vendor acquisitions

```bash
python3 scripts/instrument-rehearsal.py \
  --image spectarr-spectarr \
  --thermo imports/release-acceptance/angiotensin.raw \
  --thermo-spectra 10 \
  --bruker imports/release-acceptance/example-dda.d
```

The optional spectrum count is an independently known expectation for the specific Thermo fixture. Omit it for a different acquisition unless its count is known. The runner always requires a nonempty spectrum catalog and successful peak retrieval. It converts each supplied acquisition to mzML and compares the catalog count with an independent XML count. It samples the first and last records as well as records with the most peaks, because sparse simulated acquisitions can have empty boundary spectra. Bruker source and converted spectrum counts may differ because the readers group ion mobility scans differently.

Fixtures are read and copied into the temporary producer folder. The final check verifies that every original fixture file retains its content checksum. Vendor fixtures are not downloaded or distributed by the harness.

A TimSim-generated native `.d` dataset can be supplied through `--bruker`. Dataset generation is a separate step. This pass does not install or execute TimSim, and it does not claim that our replay reproduces TimSim's file-writing order or a real instrument's order. The same distinction applies to Thermo RAW replay.

## What the gate exercises

| Scenario | Required observation |
| --- | --- |
| Gradually growing acquisition | No upload queue item appears while writes continue within the stability window |
| Lost successful chunk response | The proxy commits a chunk to the real server, drops its response, and observes a safe retry |
| Long pause with a matching lock marker | No queue item appears while the marker remains, including after a pause longer than the stability window |
| Agent crash after a committed chunk | The actual process is killed, then restarted with its existing SQLite queue |
| Network outage during restart | The existing upload remains retryable and resumes after connectivity returns |
| Offset recovery | The same upload session resumes from the first acknowledged server chunk, rather than resending the entire file |
| Identical bytes in separate acquisitions | Separate run and artifact identities are retained, while content storage is deduplicated |
| Repeated polling | A finished acquisition is not uploaded again |
| Native directory with late members | A `.d` directory remains blocked until its marker clears, then every stored member matches its manifest and source |
| Server and agent restart | Existing run identities remain unchanged and completed queue entries remain complete |
| Downstream use | Uploaded files yield nonempty spectrum catalogs and readable peaks. Every supplied acquisition converts to mzML with readable peaks and an independently checked XML spectrum count |

The accelerated test uses a 1.2-second stability window and a 0.15-second poll interval. The disposable server uses a thirty-second job lease for restart recovery. Normal agent and server defaults remain unchanged. These timings exercise state transitions, not real acquisition duration.

## Completion detection is still a heuristic

A negative control writes an incomplete `.raw` file into a dry-run agent's watch folder, pauses longer than the stability window, and observes that the agent considers the bytes stable. No incomplete acquisition is uploaded by this control.

A result of `passed_with_documented_limitations` means the listed transport and lifecycle assertions passed, including reproduction of this limitation. It does not certify an instrument for unattended collection. A quiet file may still be acquiring, even if hashing succeeds. A successful checksum proves which bytes were transferred, not that the acquisition was finished.

Before unattended deployment, validate the actual vendor software, operating system and destination filesystem. Prefer a documented completion signal or a producer-controlled staging-to-published workflow when available. The uploader must remain read-only with respect to the instrument's output. Do not assume every vendor emits one of our recognized markers.

The tests operate on individual acquisitions. They do not infer that an entire project or sample sequence is finished. That requires a separate expected-run manifest or explicit sequence-completion signal.

## Evidence and cleanup

The runner prints its temporary evidence directory. `results.json` records each passed scenario and the known limitations. `IMAGE` records the exact server image. Agent logs, a failure summary when applicable, test data and server logs remain locally for diagnosis. The isolated container and its network are removed when the runner exits normally or handles an exception.

The local directory includes test credentials in `agent.toml` and the SQLite queue. Keep it private. Only share reviewed result files, not the whole directory.

## Vendor spectrum consistency corrections

The first native-file rehearsal passed upload and checksum verification but failed when opening a Bruker catalog entry. Extraction used OpenMassSpec native records, while viewing used a reader that grouped frames and precursors differently and did not expose those native IDs. The server returned HTTP 404 for a cataloged spectrum.

Thermo RAW viewing also exposed a representation mismatch. The tiny fixture's first scan had 180 profile peaks in extraction, while the viewer silently returned 175 centroided peaks.

When OpenMassSpec is installed, Thermo RAW and Bruker TDF viewing now use the same OpenMassSpec record identities, representations and peak arrays as extraction, then serializes them through Spxtacular's existing spectrum transport. It does not guess a replacement by position or aggregate several records into one. Tests cover MS1 and MS2 identities, peak arrays, precursor metadata, stream closure and rejection of a missing or wrong-level ID. Installations without OpenMassSpec retain the existing Spxtacular reader path. Native lookup through OpenMassSpec currently streams to the selected record, so latency on substantially larger acquisitions remains a performance item to measure.

## Scope of read-only discovery

Read-only discovery is an optional catalog mode, not a prerequisite for automatic instrument upload. The local implementation is described in the [external inventory guide](external-inventory.md). Managed ingestion remains the default when MassSpec promises to preserve an acquisition.

A first discovery version should record explicit external locations, last-seen metadata, availability and the last verified content identity. If a file moves, the old location becomes unavailable. MassSpec must not pretend that the recorded path still works, remove its history or silently substitute another file with the same name.

A later rescan could suggest a matching location after checksum verification. Equal content alone does not establish that two acquisitions are the same occurrence, so a match must preserve that distinction. Changed bytes become a new observation, not an update to a supposedly immutable artifact. Offline drives should retain their catalog records with an unavailable state.

Automatic move tracking, arbitrary filesystem crawling and external-file backup are outside that first version. Users could explicitly promote selected external acquisitions into managed storage. The original instrument-validation pass did not add external records. The subsequent local integration adds them separately and preserves managed storage semantics.

## Verified result, September 25, 2026

Both the portable and native vendor rehearsals passed against the same candidate image. The portable run retained three distinct acquisitions. The native run retained four, including the Bruker bundle. The final checks read 1,200 MGF spectra, 10 Thermo source spectra and 4,698 Bruker source records, then converted the MGF to mzML and verified all 1,200 spectra again. Peak retrieval checked MS1 where present and the first and last MS2 records, with native IDs, peak counts and profile or centroid representation matching the catalog.

The agent's 33 tests and the extractor's 34 tests passed. Lint, Python and shell syntax, workflow YAML parsing and Git whitespace checks passed. The portable gate is wired into CI but hosted CI was not run in this session. TimSim generation, a Windows workstation, physical instruments and network shares were not exercised.

The unmarked-pause negative control still reproduces the completion heuristic's limitation. This result qualifies the tested replay workflow, not every instrument's completion behavior.

See [machine-readable results](instrument-validation-2026-09-25.json). Full local request evidence and checksums are under `storage/instrument-validation/2026-09-25`. The original diagnostic failures remain in their temporary rehearsal directories.

The tested image was deployed to the existing local service after a fresh backup verified 36 artifact objects. The live catalog retained 20 runs and 44 artifacts, and all 45 checked acquisition file hashes remained unchanged. Existing Bruker MS1 and MS2 entries and a Thermo profile entry opened with their cataloged peak counts. The previous image is retained as `spectarr:before-instrument-validation-20260925`.

## External inventory and generated TimSim fixtures

The rehearsal now also exercises the [external inventory workflow](external-inventory.md) against a source archive mounted read-only in Docker. It checks discovery without managed ingestion, verification, single-file and bundle relocation, selected import, changed-content rejection, permission failure, and restart recovery. `--external-only` runs just these scenarios. The full command runs both suites.

The optional fixture generator uses the published [TimSim implementation](https://github.com/theGreatHerrLebert/rustims). It creates synthetic protein sequences, copies a supplied DDA reference, runs local CPU prediction models, and records source and output hashes. It does not contact a remote prediction service. Installation and model downloads require network access.

```bash
uv venv --python 3.12 /tmp/spectarr-timsim-venv
uv pip install --python /tmp/spectarr-timsim-venv/bin/python --torch-backend cpu imspy-simulation==0.4.2
python3 scripts/generate-timsim-fixture.py \
  --timsim /tmp/spectarr-timsim-venv/bin/timsim \
  --reference imports/release-acceptance/example-dda.d \
  --output /tmp/spectarr-timsim-fixture
```

Supply the printed `.d` directory through `--bruker`. Save the dependency freeze with `fixture.json`. The sampled sequence input is seeded, but exact output bytes across model versions or platforms are not promised. The bundled blank reference did not contain the DDA table required by the tested simulator, so use a qualified DDA reference. A generated native fixture still does not emulate the instrument control software's write order or completion signal.

The generated fixture exposed a legitimate empty MS2 record. Native reading and the catalog agreed on its zero peak count. The rehearsal now accepts empty arrays only when the catalog also records zero peaks, while still checking native identity, representation, matching array lengths, and nonempty sampled spectra elsewhere in the dataset.
