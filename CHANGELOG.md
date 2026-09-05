# Changelog

All notable changes to Spectarr will be documented in this file. Releases follow Semantic Versioning.

## Unreleased

### Added

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
