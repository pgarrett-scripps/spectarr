# Local integration validation, September 25, 2026

The external inventory workflow is implemented and running locally. Nothing was published, pushed, or deployed remotely. The implementation preserves the managed artifact contract and adds project-scoped external inventory, content verification, explicit relocation review, and selected revision imports.

## Verified results

| Area | Result |
| --- | --- |
| Complete automated suite | 489 passed: 205 backend, 130 frontend, 43 agent, 29 converter, 34 extractor, 31 MCP, 17 webhook |
| Backend coverage | 89.06 percent, above the existing 85 percent gate |
| Browser regression | 8 passed, including external inventory actions, keyboard selection, responsive tables, and existing research UI focus behavior |
| Docker workflow | Passed on exact image `sha256:933d3caba340be98301dcf1ecbf515f6495758114d43301f3e6fb12fc79b49b1` |
| External archive isolation | Actual acquisition agent ran with its source archive mounted read-only |
| Inventory and identity | No managed runs from scanning, verified file and bundle relocations, preserved history, no content-based acquisition merging |
| Import | Pinned revision imported with provenance. A same-size edit with restored modification time failed instead of substituting content |
| Unavailability and restart | Permission failure retained observations, and server/agent restarts retained entry identities |
| MCP | Real MCP search and resolution returned agent-host paths and required mappings without creating tasks |
| Scale probe | 100,000 empty acquisition files traversed in 19.35 seconds, peak memory about 82 MiB |
| Upgrade rehearsal | Fresh live backup restored independently, migrated, and verified all 36 managed objects |
| Local update | Retained 20 runs, 44 artifacts, and all 45 checked acquisition file hashes |

The scale result measures metadata traversal on this Linux filesystem. It does not measure server insertion of 100,000 records, checksum throughput, NAS performance, or scientific metadata extraction. The scan implementation caches sibling markers per directory and batches local observation writes to avoid repeated directory scans and individual durable commits for every file.

A frontend preview test timed out during an earlier heavily concurrent validation run. The complete frontend suite subsequently passed with two workers. The test runner now uses that bounded concurrency, and the final `make test` passed in full.

## Simulated and real native data

TimSim 0.4.2 generated a small DDA acquisition from synthetic protein sequences and a copied reference directory. The run used CPU prediction models, 30 synthetic proteins, a 30-second gradient, and seeded peptide sampling. Its models and dependency environment were isolated from the application environment. No research data was sent to a remote prediction service.

The generated native data yielded 247 spectrum records: 28 MS1 records, 218 nonempty MS2 records, and one empty MS2 record. Native reading and catalog counts agreed. An initial rehearsal rejected the empty record because the test required every sampled spectrum to contain peaks. That assertion was corrected after direct native inspection, without changing the reader or substituting a different spectrum.

The final rehearsal exercised native directory upload with late members, extraction and spectrum retrieval, plus the real Thermo fixture with 10 spectra and portable MGF with 1,200 spectra. MGF conversion to mzML retained all 1,200 spectra. It also retained the existing HTTP fault, offline retry, hard agent restart, duplicate-acquisition, and completion-heuristic checks.

TimSim generation and acquisition write replay remain separate. This validates the generated file through the library, not TimSim as an emulation of vendor control software timing. Physical instruments, Windows services, UNC paths, and NAS mount behavior were not available for qualification. Catalog root enrollment is currently restricted to absolute Linux-style paths. Unmarked acquisition pauses still demonstrate that stability alone cannot prove completion.

## Fixes and safeguards

- Scanners report traversal failures. Incomplete scans suppress missing-file reconciliation.
- Source file reads reject linked path components on POSIX. Bundle enumeration propagates errors, and hashing checks for changes while reading.
- Agent bundle hashing now uses the same canonical case-sensitive member ordering as the server.
- An optional producer publication marker is checked during verification and before and after upload.
- Root ownership and project visibility are enforced separately. Agent credentials cannot use the human inventory routes to bypass project scoping.
- Task creation and scan reporting are idempotent. Uploads reuse the established resumable protocol.
- Restore verification pauses external roots and cancels pending requests. Backups contain external records, not external bytes.
- Pending external imports block experiment deletion. Completed provenance survives removal of managed records through the recorded history.
- Browser selection focuses the acquisition detail region. Closing it returns focus to its acquisition button.

## Evidence and use

See [machine-readable validation](discovery-validation-2026-09-25.json) and the [setup guide](external-inventory.md). Reviewed local logs, results, the TimSim dependency freeze, fixture hashes, and screenshot are retained under `storage/discovery-validation/2026-09-25`.

The final combined rehearsal directory is `/tmp/spectarr-instrument-rehearsal-7vvm56ua`. Temporary test credentials stay in its private agent configuration and state directory. Do not publish that directory. The generated TimSim fixture is under `/tmp/spectarr-timsim-validation`. The fixture generator records hashes and configuration, not a guarantee of byte-identical output across model versions or platforms.

The pre-update backup is `storage/upgrade-backups/external-inventory-20260925/spectarr-20260925T194843Z`. The prior local image is retained as `spectarr:before-external-inventory-20260925`. An independent restore was validated at `/tmp/spectarr-discovery-restore-final`.

No real archive folder was silently enrolled. Open a project’s **External files** tab, configure a separate catalog agent, and explicitly register the folder that should be inventoried.
