# Reliability and recovery

The September 2026 reliability pass addresses the nine prioritized findings in the repository review. It retains the single-container runtime, SQLite, the existing storage layout, and the existing API list responses.

## Permissions and browsing

Project visibility is applied before listing, counting, and paginating project data. Spectrum catalog queries are explicitly read operations even though they use POST. Processing batch visibility requires access to every project represented by its selected scope.

The run endpoint accepts `project_id`, `experiment_id`, `sample_id`, `assignment_status`, and `query`. Search covers run, project, experiment, sample, and instrument names. Percent and underscore characters are treated literally. Ordering includes a stable run ID tie breaker.

Existing clients receive a list by default. The dashboard requests `page=true` and receives `items`, `total`, `next_offset`, and `experiment_counts`. CSV export follows every page of the current filter. Selection applies to the displayed page and clears when pagination or filters change.

## Upload recovery and cleanup

An upload has a process-owned file lock covering chunk writes, completion, and recovery. The operating system releases that lock after process termination. A retry can recover interrupted verification immediately after the lock becomes available.

Completion records the upload session ID in artifact metadata. If a process stops after artifact registration but before completing the upload session, recovery reuses that artifact and reconciles its automatic processing events. Completion remains idempotent after staging cleanup.

Expired sessions can reopen using their original idempotency key. A different source checksum, filename, format, role, or explicit run cannot reuse that key. Existing staged bytes are resumed when present. If cleanup has removed them, the session starts again at offset zero. Successful chunks renew the expiry period.

Every five minutes, maintenance attempts an exclusive storage lock. It skips a cycle when API mutations are active. It removes completed, failed, and expired upload payloads and unreferenced immutable objects older than one day. It preserves objects referenced by any artifact record. Cleanup failures are logged for a later retry. The sweep does not run in restore verification mode.

## Creating and verifying backups

Run these commands from the source checkout or release bundle directory:

```bash
scripts/backup.sh /srv/spectarr-backups
scripts/verify-backup.sh /srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

New backups contain `snapshot.tar`, `IMAGE`, and `SHA256SUMS`. The snapshot contains a SQLite backup, storage, and persistent runtime secrets. The image reference identifies a compatible verifier. A partial archive has a `.partial` suffix and is not a completed backup.

Snapshot creation holds an exclusive storage maintenance lock from database capture through archive completion. Reads remain available. API mutations receive HTTP 503 with `Retry-After` while the snapshot is being made. Schedule large backups during a quiet processing period to avoid interrupting active worker writes. Keep backups outside the live data directory and protect them as carefully as the original data because they contain credentials and acquisition files.

Verification checks archive checksums, SQLite integrity and foreign keys, artifact presence, recorded byte sizes, SHA-256 checksums, and every file in a vendor-directory bundle. It rejects unsafe archive paths and unsupported links. Verification runs in a separate container with networking disabled and does not require the original Spectarr instance to be running.

The verifier also accepts the earlier `database.sqlite3` and `storage.tar` backup layout. When moving a source-build backup to another host, or verifying a legacy backup, select an image containing the updated verifier:

```bash
SPECTARR_BACKUP_IMAGE=your-compatible-spectarr-image scripts/verify-backup.sh /srv/spectarr-backups/backup-directory
```

Use a trusted image reference or digest. The image ID recorded for a local source build may only exist on the original Docker host.

## Rehearsing a restore

```bash
scripts/restore-test.sh /srv/spectarr-backups/backup-directory /srv/spectarr-restore-test
```

The restore directory must not already exist. The script verifies the backup, extracts it, explicitly sets temporary data and storage mounts, and starts a separate Compose project on allocated loopback ports. It overrides inherited mount settings so the rehearsal cannot accidentally mount the active data directory.

`SPECTARR_RESTORE_MODE=true` starts only the API and private spectrum reader. Conversion, extraction, webhook delivery, and MCP workers remain stopped, and storage mutations are rejected. The script checks API health and independently verifies the restored artifact files, then stops the temporary stack. Restored files remain available for inspection.

For an actual recovery, first verify and extract the backup into a new data directory. Map `database.sqlite3` to `spectarr.db` for the new snapshot layout. Configure the restored data and storage mounts explicitly, start in restore mode, and run:

```bash
docker compose exec -T spectarr spectarr-backup verify-files /data/spectarr.db /data/storage
```

Once verification succeeds and the original instance is stopped, remove restore mode and start normal processing. Review outstanding jobs and webhook deliveries before enabling workers. A restored queue represents the point in time captured by the backup.

## Dependency and release checks

The backend lock is authoritative for the production API dependency versions. After an intentional dependency update, run:

```bash
python3 scripts/check_dependencies.py --write
make test
```

The check fails when container constraints differ from the backend lock or omit a core API dependency. Service installs use the same constraints. Python linting covers backend source and tests. Incremental type checking currently covers the backup and locking modules.

Source builds require the sibling commits listed in `DEPENDENCIES.env`. Development checkouts may have advanced beyond those versions. Supply `MSCONVERT_CLI_SOURCE`, `MZMLPY_SOURCE`, and `SPXTACULAR_SOURCE` to Make when using separate pinned checkouts. Compose accepts `SPECTARR_MSCONVERT_CLI_SOURCE`, `SPECTARR_MZMLPY_SOURCE`, and `SPECTARR_SPXTACULAR_SOURCE` for the corresponding build contexts.

Release runs execute component and integration CI, build a candidate image, and rehearse that exact digest. Stable version and `latest` tags are promoted only after the candidate rehearsal and Windows installer checks succeed. Manual workflow dispatch rehearses a candidate without promoting stable tags. Release runs are serialized.

## Validation of this pass

Validation used synthetic acquisitions and isolated temporary databases and containers. Existing repository data was not used.

| Check | Result |
| --- | --- |
| Backend regression suite | 103 passed, 86.91% coverage |
| Dashboard regression suite | 56 passed |
| Agent, converter, extractor, MCP, and webhook suites | 128 passed, no skips |
| Backend lint and incremental type checks | Passed |
| Dashboard type checks, lint, and production build | Passed |
| Version and dependency consistency | Passed |
| Backend dependency vulnerability scan | No known vulnerabilities found at scan time |
| Linux container integration | Ingestion, conversion, spectra, MCP, and webhook delivery passed |
| Concurrent ingestion and forced restart | 24 runs verified after restart |
| Backup and isolated restore | Archive, database, and required artifact checks passed |
| Browser workflow | Project creation through indexed spectra passed |

Windows installer execution, registry promotion, and proprietary vendor-file acceptance were not performed locally. Those remain release gates. The dependency scan covers the backend environment and is not a vulnerability assessment of every optional vendor reader or operating-system package in the image.
