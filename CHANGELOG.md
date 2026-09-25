# Changelog

All notable changes to MassSpec will be documented in this file. Releases follow Semantic Versioning.

## 0.4.0 - 2026-09-25

- Migrate Python to 3.14, SQLAlchemy to 2.1, TypeScript to 7, Vitest to 5, Node to 26, and the reader runtime to .NET 10.
- Pin the September stable ProteoWizard build, verify vendor conversions and read-only discovery, and close SQLite backup connections explicitly.

- Upgrade Spxtacular to 0.9 and mzMLPy to 0.10 with schema 2 support and rapidgzip access.
- Harden browser request boundaries, webhook DNS handling, converter isolation, XML parsing, and concurrent administrator setup.
- Refresh dependency locks and container images, remove runtime installer tooling, and pin CI actions to commits.

- Added local external inventory with explicit folder registration, project permissions, coverage-aware scans, content revisions, confirmed relocations, and selected resumable imports
- Added read-only MCP external search and host-scoped location resolution without changing managed artifact access
- Added optional producer publication markers and safer source reads, with consistent bundle checksum ordering
- Added read-only Docker inventory rehearsals and a small local TimSim fixture generator

- Added an isolated simulated instrument rehearsal with real agent processes, resumable HTTP fault injection, native bundle replay and a portable CI gate
- Thermo RAW and Bruker TDF spectrum viewing uses the extraction reader when available, preserving catalog native IDs, profile or centroid representation, and peak arrays
- Documented the acquisition completion heuristic and reproduced its unmarked-pause limitation without uploading incomplete data

- Simplified run processing with primary formats, stored output links, live job details, and collapsed completed history
- Unavailable artifacts are rejected as explicit conversion inputs
- Idle converter workers retry temporary maintenance responses without restarting the application
- Real-file release rehearsals now verify reimported mzML, MCP pagination and file resolution, independent scientific counts, project renaming, maintenance, restart, and restore
- Captured source archives include exact dependency changes and checksums. CI applies the pinned Sage centroiding correction and uses the tested dependency revisions

- Rebranded the workspace as MassSpec with compatible existing configuration and storage identifiers
- Replaced the wide SDRF grid with searchable entries, focused field groups, and collapsible project details and column management

- Named conversion presets now recognize gzipped outputs and index mzML without adding a duplicate gzip suffix
- RAW extraction preserves nested OpenMassSpec precursor m/z, charge, isolation windows, collision energy, and activation metadata
- The integrations page displays and copies a configured public MCP endpoint for custom ports and reverse proxies

- Long dialogs remain scrollable when profile or experiment lists exceed the viewport
- Activity queued-job cancellation with atomic server state checks, viewer write restrictions, and accurate cancelled status
- Retry controls for cancelled processing batches and recovery from transient polling failures without discarding unsaved backup settings
- Extended GUI verification of webhook controls, repository package checksums, derived-file reclamation, populated experiment deletion, navigation, and session expiry

- GUI hardening for dialog keyboard navigation, pending submissions, readable API errors, download session expiry, viewer permissions, and settings navigation
- SDRF validation and export save current edits, column editing keeps keyboard focus, and repeated imports can retry the same file
- Processing previews discard stale results, metadata-only automation stays selected, advanced profile filters survive editing, and expanded batches refresh with live progress
- Dedicated browser regression coverage for imports, spectra, SDRF, processing, settings, accounts, project membership, agent enrollment, storage previews, and experiment deletion

- Public PRIDE dataset lookup and selected acquisition downloads with a persistent server queue, configurable parallel transfers, restart recovery, cancellation, retry, integrity checks, and repository provenance
- Available PRIDE SDRF preview and import for selected acquisitions, preserving existing annotations and multiplexed sample labels
- Administrator download concurrency settings that apply without restarting, plus filename search, bulk selection, and progressive file listing for large PRIDE datasets
- Gzipped MGF and MS2 metadata extraction now streams compressed input and detects gzip content in immutable objects

## 0.3.0 - 2026-09-04

### Fixed

- Project membership filters now cover collection endpoints, overview totals, processing batches, events, and webhook delivery listings
- Viewers and read-scoped API clients can query spectrum catalogs without write access
- Run browsing and search use server-side filters and pagination, and CSV export includes every matching run
- Project, experiment, and job selectors fetch all available pages
- Completed agent uploads release their staging copy, expired uploads resume with the same retry key, and interrupted verification reuses any already committed artifact
- Periodic storage maintenance reclaims abandoned upload payloads and old unreferenced objects while preserving active work
- Backup snapshots coordinate database and storage access and verify every ready artifact and vendor-directory member
- Restore rehearsals explicitly isolate their mounts and disable processing and outbound workers
- Stable image tags are promoted from the tested candidate digest only after release gates pass
- Production API dependency constraints match the backend lock, including current Starlette security fixes
- Blocking API operations and downloads run outside the event loop, token activity writes are throttled, and run pages load related data with bounded query counts

### Added

- Administrator backup settings with persisted schedules, on-demand backups, retention, destination identity checks, and visible verification and failure status
- Periodic isolated restore checks that verify extracted acquisitions, boot the restored API, and enforce read-only recovery mode
- Optional Compose backup mount, with missing-mount protection and independent verification and restore instructions

- Browser batch import with shared project and experiment assignment, filename-based naming previews, editable samples, per-file progress, and retries that reuse already committed runs and artifacts
- Multiple allowlisted server paths can be queued together, including atomic vendor directories
- `SPECTARR_STORAGE_DIR` in the release Compose file mounts bulk artifact storage separately from the data directory, so the SQLite database can stay on local disk while artifacts live on large or network storage
- Conversion side-cars now translate paths using every mount of the Spectarr container (longest prefix wins), so a separately mounted storage root converts correctly
- The storage root is stamped with an identity marker at startup; Spectarr refuses to start when the expected marker is missing or belongs to another instance, so an unmounted storage share fails loudly instead of writing artifacts to the wrong disk
- Startup now warns when the SQLite database sits on a network filesystem (CIFS, NFS, and similar), where SQLite locking is unsafe

## 0.2.0 - 2026-08-31

### Added

- Zero-configuration production startup with persistent, automatically generated application and worker secrets
- Automatic discovery of bind mounts and named volumes used by nested conversion containers
- Release-readiness validation, clean-room rehearsal tooling, and production smoke coverage
- Public conversion-library contracts for reproducible ProteoWizard execution
- Security reporting and release governance documentation
- Unsigned Windows acquisition-agent MSI packaging with native service registration, rotating logs, and lifecycle scripts
- Dashboard controls for agent disablement and one-time token rotation
- Concurrent SQLite restart soak testing and reusable real-vendor acceptance tooling

### Changed

- The published image now starts with one Docker command and the standalone Compose file requires no environment file
- Backups now include persistent runtime secrets and work with bind mounts or named volumes
- The full local and release stack now runs as one container with one published image
- SQLite is now the only application database, with WAL mode, integrity checks, and online backups
- The dashboard and REST API now share one HTTP port
- Dashboard navigation now follows Projects to Runs to Run details, with experiments acting as project-level filters
- Run details now separate Summary, Spectra, Files, Processing, and Provenance into stable routes
- Project and run routes now reload on identifier changes and repair stale project, experiment, legacy, and tab URLs
- Loading, empty, filtered-empty, and unavailable states are now distinct throughout the project workflow
- Imports launched from a project preserve that destination and suggest its existing experiments
- Dashboard-only image rebuilds now reuse the installed backend and worker dependency layers
- OpenMassSpec is now limited to vendor acquisitions, leaving open formats to their native streaming parsers without fallback noise
- Forced metadata extraction now refreshes the stored result and removes stale warnings
- Spectrum details selected from the catalog now preserve the catalog scan identity
- Running processing jobs now honor batch cancellation through the converter worker
- Version consistency is validated against the canonical `VERSION` file
- Acquisition identity now preserves distinct occurrences with identical content while retaining checksum-based storage deduplication
- Scientific source dependencies, Python runtime packages, and base container images are now pinned for release builds
- ProteoWizard peak-picking levels now compile as valid ranges, and failed conversions preserve stderr in job errors
- Backup validation now boots and checks an independent restored Spectarr instance
- Container shutdown now cancels named ProteoWizard jobs cleanly and leaves leased work recoverable after restart

## 0.1.0

Initial release candidate for the self-hosted mass spectrometry library, processing, extraction, acquisition, webhook, dashboard, REST, and MCP services.
