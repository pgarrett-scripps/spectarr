# MassSpec project review, 2026-09-24

**Follow-up:** the [focused correctness pass](correctness-pass-2026-09-24.md) implements the source-summary policy, unknown-date handling, and recoverable library rebuild and rename discussed below. It was subsequently deployed with a verified backup. The [September 25 review fixes](review-fixes-2026-09-25.md) add existing manifest refresh, active-worker protection, and run-page updates. This review records the original findings.

**Assessment:** the use case is worthwhile. The strongest product is a local catalog that lets people and agents identify an acquisition, locate its files, understand its provenance, and choose a usable representation without guessing. The implementation already has substantial infrastructure. Its next stage should improve the truthfulness and accessibility of that information before expanding the integration surface.

This review covers the working tree, including the substantial changes already present at the start. Those changes were preserved. No production data was modified, no existing library was rebuilt, and no commits or deployments were made. Tests, destructive recovery probes, and browser imports used temporary databases and dummy files.

## The problem to solve

An acquisition's filename is a poor database key. A filename can change, the same content can appear in multiple places, a vendor acquisition can be a directory, and an MGF derived from an acquisition is not the same scientific object as the original. A filesystem browser cannot fully express those relationships.

MassSpec should answer five questions reliably:

1. What acquisition or artifact is this, and what project, experiment, and sample context is actually known?
2. Where can I open it from this computer, and is that location available now?
3. Is it an original, a converted representation, or an analysis result?
4. What created it, from which input, with which recipe and tool version?
5. What is known about integrity, extraction, and processing, and what remains unknown?

This is useful for both a person scanning a table and an agent preparing an analysis. The differentiating work is the reliable mapping between scientific identity, file identity, and usable location. Conversion, QC browsing, and repository imports support that core.

There is an important scope distinction. The current application manages imported copies. It does not catalog every mass spec file already scattered across the PC in place. Path import copies content into object storage. Hard links avoid another copy for the readable library on the same filesystem, but they do not eliminate the original import copy. A future read-only folder catalog would address a different, valuable workflow and needs a separate unmanaged-location model.

## Where the implementation stands

| Area | Assessment |
| --- | --- |
| Identity and storage | Good foundation: stable IDs, SHA-256 object identity, bundle manifests, a readable library, and derivation links |
| Ingestion | Multiple useful paths exist: browser batch import, server path import, resumable acquisition-agent upload, and durable PRIDE import |
| Processing | Recipes, revisions, fingerprints, batch previews, retries, leases, and reclamation already exist |
| Metadata and spectra | Artifact extraction and persistent spectrum catalogs are useful, but the run summary currently mixes source and derivative observations |
| Agent access | A REST-backed MCP adapter avoids direct database coupling. Discovery and explicit file resolution were incomplete and are improved in this review |
| Recovery | Backups, restore verification, storage identity checks, and upload recovery have meaningful tests. Library reconstruction still has a filesystem transaction gap |
| Interface | Projects, experiments, runs, and per-run tabs provide a workable hierarchy. Core file access and truthful status deserve more prominence |
| Deployment | The single-container and SQLite design fits the current scope. Docker mount mapping and the converter's Docker socket increase setup complexity |
| Validation | Broad automated coverage exists. Passing it does not establish performance or scientific accuracy across all vendor data |

Keep the current deployment shape while measuring actual limits. A database replacement or additional distributed services would not resolve the identity, summary, and location problems found here.

## Code review findings

**P1, mitigated: a rebuild could destroy the last readable copy of an acquisition.** In [library.py](../backend/src/spectarr/library.py), reconstruction cleared the whole readable library before checking that its source objects existed. I ingested a file, removed only the internal object pathname, and kept the readable hard link. Rebuild then returned HTTP 500 and removed the surviving link. A second, unaffected readable artifact also disappeared. The new preflight checks every ready object's presence and type, including bundle payload members, before clearing anything. Regression tests cover a missing file object and a missing bundle member. This protects the reproduced case, but it is not a full integrity scan or an atomic rebuild.

**P1, open: derivative extraction overwrites acquisition-level scientific summaries.** In [platform_api.py](../backend/src/spectarr/platform_api.py), `create_extraction_result` merges every artifact's normalized metrics into the run. Both `run_view` and `latest_run_qc` select the most recently updated extraction across artifacts. A source with 100 spectra followed by a derived MGF with 40 spectra changed both the run's headline count and its QC count to 40. The result depends on completion order. Fix this by keeping observations artifact-specific, choosing an explicit acquisition-summary policy, and returning the selected artifact ID and fallback reason. Preserve support for RAW files whose usable metadata comes from mzML, without silently treating a filtered MGF as the acquisition. Validate the policy using real source and derivative pairs before using run counts for scientific decisions.

**P1 for network exposure, open: HTTP MCP does not authenticate its callers.** In [cli.py](../services/mcp/src/spectarr_mcp/cli.py), incoming requests go directly to a server configured with one backend credential. Incoming Authorization, Origin, and Host are not validated. Setting `SPECTARR_API_KEY` configures the adapter's backend identity, not authentication for connecting MCP clients. Compose defaults to loopback, which limits exposure. Changing its common bind address to a LAN address also publishes MCP there. A reachable caller receives the configured adapter's access, and write mode makes its write tools reachable too. Establish a caller-authentication boundary and Origin/Host checks before offering shared network MCP. Per-user stdio adapters with scoped API credentials fit the current local workflow more naturally.

**P2, open: reconstruction and project rename are not atomic filesystem operations.** Even with preflight, [library.py](../backend/src/spectarr/library.py) clears the live view before rematerializing it. A copy failure, permission error, or interruption can leave a partial view. [api.py](../backend/src/spectarr/api.py) commits a project rename before rebuilding the entire library. The maintenance dependency uses a shared mutation lock, so it does not serialize all rebuilds against ingestion. Build and validate a replacement view, journal its transition, coordinate writers, and retain the old view until publication succeeds. Test interruption at each boundary, including copied libraries on a separate filesystem.

**P2, open: health and storage displays overstate what they measure.** `/library` calls the library healthy when the number of non-null database paths matches the number of ready artifacts. The pre-fix failed rebuild above still returned `healthy: true` with its files gone. `/storage` reports logical artifact bytes as `usedBytes`, compares them with physical volume capacity, and hardcodes healthy status. Deduplicated data, copied views, staging files, other applications, and separate library filesystems can all make that ratio misleading. Separate catalog consistency, observed file availability, verification status, logical bytes, and actual disk free space. The new per-artifact resolver checks existence on demand and explicitly says it does not reverify checksums.

**P2, open: unknown acquisition dates are presented as measured dates.** `run_view` in [api.py](../backend/src/spectarr/api.py) substitutes `created_at` when `acquired_at` is absent. The probe confirmed that an acquisition without a supplied date returned an acquisition timestamp identical to its import timestamp. An agent can mistake that for experimental chronology. Return unknown acquisition time explicitly, preserve import time separately, and update UI rendering and CSV exports together.

**P2, fixed: search could not find a file by its original identity.** Search previously covered descriptive hierarchy names but omitted original artifact filenames, managed paths, IDs, and hashes. These are often the only evidence a user or agent starts with. Search now includes those fields. Filename matching keeps literal wildcard escaping, and artifact matching uses a relationship existence condition so multiple matching artifacts do not duplicate runs. Tests verify duplicate matches and project access boundaries.

**P2, fixed: agent discovery lacked scope discovery, explicit pagination results, and a file resolver.** MCP now provides `list_projects`, `list_experiments`, and `resolve_artifact`. `search_runs` accepts project, experiment, and sample IDs and returns `items`, `total`, and `next_offset`. The resolver uses an OpenAPI-described read endpoint, with server-path scope, download availability, directory identity, checksum semantics, and provenance IDs. Read-mode tool discovery omits disabled write tools. Invalid new pagination and identifier inputs fail before a REST request. See the [contract and compatibility notes](agent-file-discovery.md).

**P2, fixed for new materialization: compressed library names lost their format suffix.** `original_extension` only preserved `.mzML.gz`. An imported `.mgf.gz`, `.ms2.gz`, or `.msp.gz` could become a tokenized name ending only in `.gz`, losing an important format cue for external readers. Compound extensions and collision suffixes now retain the original format. Existing materialized paths are deliberately not rewritten automatically.

**P2, partially improved: the Files interface suggested unsupported access and current verification.** It offered download actions for directory bundles even though the download API rejects them. Those actions are now disabled, directory guidance is available through Locate file, and Files and source provenance display stored status rather than presenting a recorded checksum as a fresh verification. The broader lifecycle/status model still needs work. In particular, the existing API's `verified` display value remains for compatibility.

## What I would simplify

Make the primary experience **find, inspect, use**. Show original filename, source or derivative role, format, usable location, project/sample context, and a concise availability state. Keep acquisition time separate from import time. Let users expand provenance, spectra, and advanced processing when needed.

Keep the existing spectrum viewer and conversion pipeline, but make them optional support for organizing data. A user should be able to catalog and locate an acquisition without waiting for every derivative or deep QC computation. Expose pending extraction as its own state.

Group instrumentation, webhooks, automation, repository submission, and server administration as advanced workflows. I would not delete these working features during this review. Their navigation priority can be reduced without losing capability.

The current browser download helper buffers the response as a Blob before saving it. For large acquisitions, investigate an authenticated streaming download design with clear progress, cancellation, and resume behavior. The server already streams file bodies, but the browser path is not yet a complete large-file transfer experience.

## Development sequence

| Order | Deliverable | Acceptance condition |
| --- | --- | --- |
| 1 | Trustworthy summaries and states | Source metrics remain stable after MGF extraction. Fallback metrics identify their artifact. Unknown dates remain unknown. Stored, available, verified, extracted, and converted are distinct |
| 2 | Safe library maintenance | Missing inputs, disk-full failures, concurrent imports, renames, and forced restarts preserve the old usable view or recover deterministically |
| 3 | Complete local agent workflow | An agent finds a file, resolves its actual host mount or authenticated download, selects the appropriate derivative, and records an analysis result without guessing paths or modifying originals |
| 4 | Read-only folder discovery | Preview existing files without copying them. Record unavailable external drives explicitly. Let users opt into managed ingestion with a storage estimate and duplicate-content preview |
| 5 | Analysis integration | Register an external analysis with explicit input artifact IDs and hashes, parameters, tool version, and output artifacts. Then build one integration against that contract |

For an eventual Sage integration, reuse the artifact and provenance model rather than identifying inputs by filename or adding another special-purpose filesystem hierarchy. A first useful boundary is analysis-run registration plus manifest export. Search-engine scheduling can follow when that boundary works reliably.

The folder-discovery model must distinguish managed objects from external references. An external file can move, change, or go offline. It cannot inherit the same immutability and availability claims as managed storage. Keep stable artifact identity, content identity, and observed locations separate.

Refactor incrementally around these workflows. `api.py` and `platform_api.py` combine many domains and duplicate dashboard-oriented serialization. Extract catalog queries, artifact access, ingestion, and scientific-summary selection behind typed response contracts. The new artifact-access module is a small start. Preserve existing routes while moving logic. Expand type checking beyond the two currently configured modules as these boundaries become explicit.

## Validation performed

| Check | Result |
| --- | --- |
| Baseline backend suite | 157 passed, 88.15% coverage |
| Updated backend suite | 172 passed, 88.23% coverage, configured 85% threshold satisfied |
| Backend Ruff and configured mypy checks | Passed |
| Updated dashboard suite | 109 passed across 24 test files |
| Dashboard typecheck, ESLint, production build | Passed |
| Acquisition agent suite | 33 passed |
| Converter suite | 27 passed |
| Extractor suite | 30 passed |
| MCP suite | 30 passed, including the HTTP check with local socket access |
| Webhook suite | 17 passed |
| Version and dependency consistency scripts | Passed |
| Isolated Chromium workflow | Real API import, original-filename search, file resolution, clipboard copy, and narrow-viewport overflow check passed |
| Targeted recovery and summary probes | Reproduced derivative-summary overwrite, fabricated acquisition date, destructive missing-object rebuild, and false library health |

The initial backend tests stalled under the execution sandbox's asyncio restrictions. A focused run outside that sandbox passed, followed by the full baseline and updated suites. Service tests used their existing configured virtual environment. A preliminary attempt to collect all service tests through the backend environment was invalid because of missing service dependencies and duplicate module names. The reported service results use the repository's separate unittest discovery layout.

Browser and recovery probes used synthetic files. I did not rerun the complete Docker release rehearsal, all Playwright workflows, real vendor conversion acceptance, long-duration acquisition monitoring, network-share disconnection tests, or production-scale ingestion. Earlier acceptance reports are useful historical evidence, not results from this review. No claims here establish release readiness for every supported instrument.

The next real-data gate should cover one single-file vendor acquisition, one native directory bundle, and an open-format source. For each, exercise import, extraction, conversion, search, agent resolution, restart recovery, and independently verified source identity. Include offline storage and realistic file sizes. A 10,000-run catalog benchmark should measure query counts, import latency, manifest rewrite cost, and agent pagination before choosing new infrastructure.
