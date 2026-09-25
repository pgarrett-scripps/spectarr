# Read-only discovery proposal

Status: design proposal, September 25, 2026. The subsequent local implementation and qualified scope are documented in [External inventory](external-inventory.md). This document describes an implementation plan based on the current working tree. It does not describe a deployed feature. No application code, live data, or container was changed for this investigation.

## Recommendation

Add an optional external inventory alongside the managed library. Its promise should be: **find acquisitions where they already live, show when and how their location and content were checked, and preserve that history when the files move or change.**

The feature belongs in MassSpec because finding the correct acquisition is part of managing mass spectrometry data, especially for AI agents. It should not become a general filesystem indexer, synchronization service, or backup tool. Managed import remains the boundary for preservation, conversion, spectrum viewing, and other existing processing promises.

For the first version, keep external entries separate from `Run` and `Artifact`. Do not make `storage_key` point at an external path or mark an external file as an immutable artifact. That would break assumptions throughout storage, processing, reclamation, backup, and provenance. An external entry becomes a managed run and artifact only through an explicit import.

## Minimum useful workflow

1. An operator configures an allowed archive folder on the machine that can actually read it, using an agent in a new catalog-only mode. The agent's state stays outside the archive. The folder is attached to one MassSpec project and receives a stable root ID and a human-readable name such as “Proteomics archive.”
2. MassSpec previews acquisitions without copying their contents. A compact list shows name, format, location, size, availability, and last observation. A vendor directory appears as one acquisition. These are external entries, not newly imported runs.
3. The user searches the inventory through the UI or MCP. File facts appear immediately. Content verification runs separately for selected entries, or through an explicitly enabled background policy. Unhashed entries remain useful but cannot claim duplicate content or a verified relocation.
4. If an archive disconnects, its records remain searchable with “Location unavailable” and a last-seen time. If a file disappears during a successful scan of a reachable folder, its location says “Not found.”
5. After a rescan, the user can review a possible new location, verify the bytes, and confirm a relocation. MassSpec preserves both paths and the confirmation event. It does not silently replace an acquisition with another same-name or same-content file.
6. The user can explicitly import a selected verified revision through the existing uploader. The resulting artifact records the external entry, revision, and location that supplied it. The external record remains as history.

Use one “External files” view within the project, rather than adding several top-level dashboards. Show scan, verification, and import progress only when relevant. Detail disclosure can contain hashes, root identity, scan errors, and location history. Main library counts and managed-storage totals must not include unimported external files.

## What exists today

| Capability | Current behavior | Change needed |
| --- | --- | --- |
| Instrument discovery | The acquisition agent recursively discovers supported files and atomic `.d` and `.raw` directories | Reuse format and bundle recognition, with catalog-only reporting |
| Read-only source handling | The agent reads sources, skips discovered symlink entries, waits for stability, and hashes content | Validate root selection before resolution and qualify Windows junction and reparse-point handling |
| Dry run | Updates local stability observations without contacting MassSpec or uploading | This is not a persistent server inventory |
| Acquisition identity | Upload queue occurrence identity is path and signature, not checksum alone | External identity must persist independently of paths and observation retention |
| Managed content | Artifacts require a storage key and SHA-256 and link to a run | Leave this contract unchanged |
| File resolution | REST and MCP resolve managed artifact IDs to API-server paths and availability | Add a separate external resolver with explicit host and root scope |
| Integrity reporting | Managed resolver checks existence and type, and explicitly does not rehash | External resolution must also distinguish observation from verification |
| Shared content | Separate acquisitions can share a content digest | Keep separate external entries even when their content matches |
| Native bundles | Existing manifest includes root name and member paths, sizes, and hashes | Add a separate comparison fingerprint for rename matching |

Relevant source locations:

- `services/agent/src/spectarr_agent/discovery.py`: `AcquisitionScanner`, `snapshot`, and `hash_candidate`.
- `services/agent/src/spectarr_agent/service.py`: `scan_once` currently leads to upload queuing, apart from dry run.
- `services/agent/src/spectarr_agent/state.py`: `observations`, `upload_queue`, and local agent identity.
- `backend/src/spectarr/models.py`: `Artifact`, `Run`, `Agent`, and `UploadSession`.
- `backend/src/spectarr/storage.py`: immutable objects, native bundle manifests, library materialization, and reclamation boundaries.
- `backend/src/spectarr/artifact_access.py`: version 1 managed file access contract.
- `backend/src/spectarr/platform_api.py`: upload completion and `bundle_manifest_digest`.
- `backend/src/spectarr/auth.py`: current scope and project visibility behavior.
- `services/mcp/src/spectarr_mcp/catalog.py` and `server.py`: tool contracts and dispatch.

The current scanner silently skips inaccessible directories and absent roots. An empty discovery result therefore cannot currently distinguish an empty folder from a failed scan. That must change before it can drive missing-file reconciliation.

Configuration currently resolves watch paths before discovery. The traversal's symlink checks therefore do not prove that an originally configured symlink root was rejected. External root enrollment needs its own explicit validation of the original path, resolved target, and permitted mount identity.

## Identity and changes

Keep four concepts separate: an acquisition occurrence, a content revision, a filesystem location, and a recorded observation. A path is a location. A hash identifies bytes. Neither alone establishes the identity of a biological sample or acquisition.

| Event | Required behavior |
| --- | --- |
| File moves within a root | Retain the old location. Discover the new location. Offer a verified relocation suggestion, then require user confirmation |
| File moves to another registered root | Same behavior, subject to project visibility. Do not crawl outside configured roots looking for it |
| File moves outside all roots | Keep its history and mark the known location not found after a complete successful scan |
| Root goes offline | Mark root unavailable. Preserve prior file observations. Do not infer that every file was deleted |
| Agent stops reporting | Mark observations stale. Do not assert that the drive or files are missing |
| Access is denied to a subtree | Mark the scan partial and that subtree unverified. Do not reconcile missing entries in that subtree |
| Different drive appears at the same path | Report root identity mismatch where detectable. Do not treat it as the original archive or mass-delete records |
| Same path now has changed content | Mark its prior revision superseded at that location and record a new revision after verification. Preserve historical hashes and imports |
| Same name, different content | Separate entries unless a user explicitly identifies a replacement. Never silently relocate |
| Identical content at two paths | Separate occurrences with a content match. Do not automatically merge runs or call one a move |
| Metadata changes but bytes do not | Reverify if needed and append an observation of the same revision |
| File disappears and later reappears | Reobserve and verify. A matching digest can reconnect location history, but cannot prove it is the same acquisition occurrence |

Filesystem IDs, inode numbers, volume IDs, and file creation times can support a suggestion. They are hints rather than portable acquisition identities. They can be unavailable, reused, or change across a copy. Even a unique digest match does not prove that a new file is a move rather than a separate acquisition containing identical bytes.

An unmounted drive can leave an existing but empty mount directory. Root existence alone is insufficient. Where reliable mount identity cannot be established, report root verification as unknown and suppress absence reconciliation until an operator revalidates the root. Never write a marker into the source to establish identity.

Confirmation joins location history to the chosen existing entry and records any replacement candidate as an alias with an audit event. It must not erase either history. Ambiguous matches require the user to select the occurrence. A content change at one continuing location is conservatively represented as a revision, without inventing a new scientific acquisition. The user may later split it into a separate entry if it was an overwritten acquisition.

## Native bundles and verification

A `.d` or directory-shaped `.raw` remains one unit. Do not descend into it and create separate acquisitions for database, binary, index, or marker files. Verification checks every member, including membership changes, and rejects links or unsafe member paths. A directory merely existing does not establish that its acquisition is intact.

The existing managed bundle SHA-256 includes `root_name`. Renaming `sample.d` to `renamed.d` therefore changes that digest even with identical members. Preserve this established checksum contract. Introduce a separately named, versioned comparison fingerprint, such as `bundle-content-v1`, over the canonical ordered list of member relative paths, byte sizes, and SHA-256 values. Exclude the outer folder name. Include a domain/version marker and specify UTF-8 encoding and deterministic ordering. Do not lowercase member names or normalize distinct names into collisions.

This fingerprint can propose a folder relocation. It is not an artifact SHA-256, and it does not mean two acquisitions are the same occurrence. Renaming an internal bundle member changes it, intentionally. For a single file, comparison uses the full byte SHA-256.

Unknown sidecar-dependent formats need explicit capability labels. In the initial supported set, qualify single-file Thermo RAW, mzML, mzXML, MGF, and MS2, plus Bruker `.d` and directory `.raw` inventory behavior. Standalone `.tdf`, `.baf`, `.tsf`, or WIFF files must not be presented as complete native acquisitions merely because the current scanner recognizes their suffixes. Broader compound-format import can follow separate fixtures and grouping rules.

## Readiness and scan cost

Discovery may list a file while it is changing, with readiness `changing`, `blocked`, or `unknown`. Content verification and import wait for the existing stability checks. Preserve the distinction between `stable_by_observation` and an explicit producer completion signal. The simulated instrument tests have already shown that a sufficiently long unmarked pause can pass the stability heuristic.

Record readiness evidence and its time. Do not label an acquisition “complete” merely because hashing succeeded. Before import, recheck the selected revision, then let the server verify the uploaded bytes and bundle manifest. If the bytes changed, fail the pinned import with a conflict and request a new revision selection. Do not silently import the replacement.

Start with scheduled or manually initiated scans, not another ten-second recursive poll over an archive. Bound directory traversal, verification concurrency, I/O bandwidth, and retry intervals. Use one scan per root, cancellation, progress counters, and resumable report batches. File notifications can be an optimization later, but cannot replace reconciliation.

Cheap scans inspect names, sizes, timestamps, and bundle membership. Hashing remains a separate expensive operation. A terabyte-scale archive should become searchable before all its contents are read. Store checksums only with their verification timestamp, algorithm, and pre/post observation signatures. Size and timestamp equality is a cache hint, not proof of unchanged bytes. Full verification must detect a same-size edit with a preserved modification time.

Commit a scan generation only after the scanner reports its coverage and completion. A canceled, crashed, truncated, or partially inaccessible scan must never cause global missing-file transitions. Record errors by subtree and reconcile only proven coverage. Initial implementation can conservatively skip all missing reconciliation after any traversal error.

## Proposed additive data model

| Entity | Minimum fields and invariants |
| --- | --- |
| `ExternalRoot` | Stable ID, project ID, owning agent ID, label, configured root path and path flavor, optional volume/share identity evidence, enabled state, scan policy, last contact and successful scan. One owning agent and one project in version 1 |
| `ExternalEntry` | Stable occurrence ID, project ID, first-seen time, displayed name and format, current revision pointer, optional confirmed alias target. No required run or artifact |
| `ExternalRevision` | Immutable revision ID, entry ID, kind, byte size, file count, nullable file SHA-256, nullable bundle manifest and comparison fingerprint, verification time and method. Unverified observations do not fabricate a verified revision |
| `ExternalLocation` | Stable ID, entry ID, root ID, relative path, path comparison key appropriate to that root, first/last seen, last complete scan ID, current availability, observation signature, current revision pointer. Retired paths remain historical |
| `ExternalScan` | Root ID, generation ID, start/end, state, coverage and errors, counters and idempotent batch sequencing |
| `ExternalObservation` | Location ID, scan ID, observed time, file facts, availability, readiness evidence, verification outcome and optional revision ID. Retain revision and relocation history even if routine unchanged observations are compacted |

Add a small import provenance link from an external revision and location to the resulting managed artifact. Prefer a foreign-key relation over an unvalidated free-text ID. This does not make an external entry a parent artifact or change conversion recipes.

Enforce a unique active path within a root, with explicit handling of case sensitivity and path flavor. Reject overlapping configured roots on the same agent in the first version to avoid double discovery and conflicting ownership. Reject roots inside managed object storage, the materialized library, staging, or the agent state directory. Do not allow root paths to be rewritten through ordinary scan reports.

## Proposed API and AI-agent contract

Keep `GET /api/v1/artifacts/{id}/access` and MCP `resolve_artifact` unchanged. Add external endpoints rather than expanding their existing literal types and confusing older clients.

| Endpoint family | Purpose |
| --- | --- |
| `/api/v1/external-roots` | Administrative registration, project binding, list, pause and retirement |
| `/api/v1/external-roots/{id}/scans` | Begin, report paginated observations, and finish a scan generation from its owning agent |
| `/api/v1/external-entries` | Project-scoped search, filters, total and stable cursor pagination |
| `/api/v1/external-entries/{id}` | Revisions, observed facts, location history, and managed import links |
| `/api/v1/external-entries/{id}/access` | Read the latest known location and verification evidence without initiating a scan or hash |
| `/api/v1/external-entries/{id}/verify` | Explicit queued verification request, resolved by the owning agent within its local root allowlist |
| `/api/v1/external-entries/{id}/relocations` | Review candidates and record a confirmed location association with expected revision checks |
| `/api/v1/external-entries/{id}/imports` | Explicit import of a pinned revision through the established upload protocol, with idempotency and project targeting |

The exact REST verbs and request schemas remain implementation work. Scan and verification requests are writes to catalog state even though source access is read-only. MCP read mode should add only `search_external_entries` and `resolve_external_entry`. It must not silently initiate hashing, imports, or relocation confirmation.

External access responses need `schema_version`, entry and revision IDs, `storage_mode: external`, root and agent IDs, host label, path flavor, root-relative path, observed availability, `observed_at`, root contact time, freshness, and integrity evidence. Use `path_scope: agent_host`, not `api_server`. Return no managed `download_url` and no inferred container or user-PC path.

A caller can map a known root ID to its own explicitly configured local mount. For example, root ID `archive-a` may correspond to a UNC path for the acquisition agent and `/mnt/archive` for an analysis host. It is not sufficient to substitute path strings across operating systems. Mapping must respect relative-path components, reject traversal, and verify that the intended root is mounted. If no mapping exists, return `mapping_required`, not an invented usable path. A caller should revalidate access locally before use. An old observation does not promise current availability.

Do not accept arbitrary filesystem paths or shell commands from an LLM as verification targets. Requests select registered IDs, and the owning agent validates containment again at open time. Protect against symlink, junction, reparse-point, and path-replacement races. Root configuration is local administrative configuration, not a remote command to crawl a workstation.

Use existing project visibility for readers, plus explicit root ownership for reporters. Current agent principals have broad library scopes and bypass project visibility checks. Do not assume that is a sufficient boundary for new path inventories. Reports must be constrained to the registered agent/root/project, with bounded member counts, path lengths, and payload sizes. Paths can reveal research metadata, so unauthorized searches, totals, hashes, and relocation suggestions must not leak other projects.

## First-version boundary and compatibility

Include explicit root registration, catalog-only agent operation, metadata inventory, selectable verification, honest availability, search and resolution, manually confirmed relocation, and selected import. Preserve inventory history across agent and server restarts. An external source is always opened read-only. Database and queue updates live in application state, outside the source root.

Selected managed import is included in the proposed first feature release as the final delivery slice. The earlier inventory pilot can ship without it, clearly labeled as discovery only. Do not expand that slice into direct processing of external files.

Exclude automatic relocation or merging, filesystem repair, moving or deleting sources, arbitrary whole-PC crawling, folder synchronization, external-file backup, cloud-object providers, inferred project completion, direct external conversion, and direct external spectra browsing. Users import when they need those existing managed processing capabilities. Avoid automatic expensive metadata extraction in the first pass. Additional scientific metadata should be introduced only with bounded read-only readers and revision-specific results.

Use additive database tables and an optional new agent mode. Existing agents continue uploading without a configuration change. Leave all existing artifact paths, checksums, manifests, backup contents, and job contracts intact. Advertise catalog-reporting capability during agent registration so an older server or agent fails clearly rather than falling back to upload.

Backups include the new catalog and history, but explicitly exclude external bytes. Restoration preserves records as stale until their agents and roots are revalidated. Deleting or retiring a root removes or disables catalog tracking according to retention policy, never source files. Existing reclamation and library rebuilding must not enumerate external roots.

## Acceptance gates

Each delivery slice must pass its applicable P0 gates before a local pilot. The inventory-only pilot does not require the later relocation or managed-import features. All gates must pass before the complete first feature release:

1. A read-only mounted test root inventories single files and native bundles while source byte hashes, permissions, and names remain unchanged. Access-time changes imposed by the filesystem are not misrepresented as application writes.
2. Discovery creates no managed runs, artifacts, object files, or processing jobs. Managed statistics and existing API contracts are unchanged.
3. Root outage, agent outage, partial traversal, canceled scan, and permission failure each preserve history and produce distinct truthful states. None generates false mass deletion.
4. Move, rename, same-name replacement, duplicate bytes, and deletion/recreation fixtures never silently merge acquisitions. Confirmed relocation retains both locations and an audit event.
5. Renaming the outer `.d` folder preserves its comparison fingerprint while its legacy manifest digest changes. A changed, missing, or renamed inner member invalidates the content match.
6. A changed file during hashing or import fails the pinned operation. Full verification catches same-size changes with restored modification time. Prior revisions and managed imports remain intact.
7. Traversal, symlink escape, root replacement, case collisions, and Windows junction/reparse tests cannot expose data outside allowed roots. Agent A cannot report or request reads for agent B's root.
8. UI and MCP show identical IDs and status semantics. An offline host never yields a supposedly local usable path. Missing mount mappings are explicit. Read tools do not cause mutations.
9. Project authorization protects list results, direct lookup, counts, content matches, paths, and reporting. A forged upload provenance link or revision mismatch is rejected.
10. Import retries create one intended managed occurrence, preserve acquisition distinctions for identical bytes, and verify content on the server. Import never modifies or removes the source.
11. Restart during reporting is idempotent. Out-of-order reports cannot roll back a newer scan. Backup/restore retains catalog history without claiming external bytes were backed up.

P1 gates should follow before broad rollout:

- Measure traversal and search with at least 100,000 filesystem entries and large nested bundles. Record time to first searchable results, peak memory, scan I/O, and cancellation latency. Set performance budgets after measuring the test machine rather than inventing universal throughput guarantees.
- Exercise Windows service access, UNC shares, disconnected and reattached drives, Unicode and case-sensitive paths, NAS permission failures, and recovery after an agent state database is lost.
- Test long acquisition pauses, delayed bundle members, ignored markers, and producer completion evidence using the existing simulated-instrument harness. Inventory visibility must not be confused with readiness to import.
- Verify checksum caching, scheduled verification, limited bandwidth, and report retention on a representative research archive. Ensure repeated cheap scans do not repeatedly hash unchanged multi-gigabyte files.

## Remaining decisions and delivery order

Recommended defaults are one project per root, one owning scanner per root, on-demand scans initially, selected content verification, and explicit relocation confirmation. This avoids speculative automation while making the inventory useful.

Decisions that need a real archive pilot are scan cadence, verification budget, history retention, and which Windows or NAS identity evidence is dependable. The first release should document supported root filesystems and formats rather than claiming universal move tracking. Hostile concurrent source mutation cannot be made atomically safe by a catalog alone. Strong reproducibility continues to require a managed snapshot.

Implement in three reviewable slices:

1. Catalog-only scanning, additive tables, project isolation, coverage-aware status, and basic UI/MCP search. Prove no source writes and no managed-library side effects.
2. Revision verification, bundle comparison fingerprints, relocation review, and explicit root mapping. Prove the move and replacement cases.
3. Pinned managed import with provenance and restart recovery. Reuse the current upload protocol and simulated-instrument test system.

The first useful milestone is a searchable real archive that tells the truth when disconnected or reorganized. Reliable import and scientific processing remain available through the established managed path.
