# Spectarr 0.3.0

This release adds batch import and managed backups, and improves access control, upload recovery, library browsing, and release verification.

## New features

- Import several files or allowlisted server paths together. Share project and experiment assignments, preview run names, edit samples, follow progress, and retry failed items without duplicate acquisitions.
- Schedule verified backups from **Settings → Backups**, run them on demand, retain a configured number of snapshots, and periodically test recovery with an isolated API instance.
- Mount bulk artifact storage separately from the local SQLite database. Converter paths follow the container's mount map, and storage identity checks detect missing or incorrect storage mounts.

## Reliability improvements

- Project visibility applies consistently to collections, counts, processing batches, and related records. Viewers and read-scoped API clients can query spectra.
- Run browsing, search, and exports cover the complete matching library. Selectors load all available pages.
- Interrupted and expired uploads recover safely, completed uploads release staging space, and maintenance removes abandoned data while protecting referenced objects.
- Backup verification checks artifact and vendor-directory checksums. Failed restore checks prevent retention from removing older snapshots until recovery is verified again.
- Blocking API work runs outside the event loop. Dependency constraints match the tested backend lock.
- Stable container tags are promoted only after the automated release gates succeed.

## Installation and backups

Download the release archive for the Compose configuration, acquisition-agent wheel, backup and restore scripts, and operating guides. The container image is `ghcr.io/pgarrett-scripps/spectarr:0.3.0`.

Managed backup scheduling starts disabled. Configure an existing backup directory and start with both `compose.yaml` and `compose.backups.yaml`, then enable the policy in **Settings → Backups**. Full snapshots and restore checks need additional disk space. The bundled backup guide explains setup and independent recovery.

The browser import queue belongs to the current page. Keep it open until the batch finishes. Use the acquisition agent for transfers that must resume across application restarts.

Windows note: The acquisition-agent installer is unsigned. Windows may display an unknown-publisher warning during installation. Verify the attached SHA-256 checksum.
