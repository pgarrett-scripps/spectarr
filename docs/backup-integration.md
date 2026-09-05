# Managed backups

Spectarr can schedule full backups to a separately mounted directory, verify each snapshot, apply retention, and periodically rehearse recovery. Administrators manage the policy and inspect results under **Settings → Backups**. Scheduling starts disabled.

## Configure the destination

Choose an existing directory outside the live data, storage, and library directories. A separate disk or a mounted NAS can provide a separate failure domain. The dashboard reports when the destination shares a filesystem with live data.

Make the directory writable by Spectarr's configured UID and GID. Use a protected destination because snapshots include runtime credentials and acquisition files. The optional Compose override refuses to create a missing host directory.

From the source checkout or release bundle, start with both Compose files:

```bash
SPECTARR_BACKUP_DIR=/mnt/lab-backups/spectarr docker compose -f compose.yaml -f compose.backups.yaml up -d
```

Persist `SPECTARR_BACKUP_DIR` in the installation's environment file and keep both Compose files in subsequent startup commands. The backup override is supplied with the release bundle. For a direct container deployment, bind the destination to `/backups` and set `SPECTARR_BACKUP_ROOT=/backups`.

Spectarr records a destination identity marker on first configuration or manual backup. Subsequent runs require that marker to match the installation's saved identity. A missing mount or unexpected destination produces an error. Move the entire destination, including its hidden marker and instance folders, when changing backup storage.

## Configure the policy

Open **Settings → Backups** and choose:

- Whether scheduled backups are enabled
- The backup time in UTC and interval in days
- How many verified snapshots to retain
- The interval between restore checks

The default policy, once enabled, runs daily at 03:00 UTC, retains three snapshots, and checks a restore every 30 days. The first managed backup always gets a restore check. Settings persist beside the database and survive a restart. If a scheduled time passes while Spectarr is stopped, it performs one overdue backup after restart and advances the schedule. Failed scheduled attempts advance to the next scheduled time, with the error retained in the dashboard.

**Back up now** also works with the schedule disabled. **Test latest restore** checks the newest verified snapshot on demand. Operations continue on the server after closing the settings page. Only one managed backup or restore operation can run at a time.

The status page shows the current operation, next scheduled run, last verified backup, last successful restore check, available space, failures, and the newest 50 retained snapshots. Failures are also written to server logs. This integration does not send email or external notifications.

## Consistency, verification, and retention

Each snapshot coordinates SQLite capture with the storage maintenance lock. Reads remain available, while application writes receive retryable HTTP 503 responses during copying. Verification and restore checks release that storage lock so normal processing can continue. Schedule large copies during quiet processing periods.

These are full, uncompressed snapshots. Each consumes space for another archive of the data. Restore rehearsals temporarily need an additional extracted copy. A free-space preflight includes a reserve, and write failures leave previous verified backups intact. The reserve defaults to 256 MiB and can be changed with `SPECTARR_BACKUP_MIN_FREE_BYTES`.

Snapshots are created inside a private temporary folder. Spectarr verifies SQLite integrity, foreign keys, required artifacts, sizes, and checksums before publishing a completed snapshot. It also checks every vendor-directory member. Each installation uses its own `spectarr-INSTANCE_ID` folder at the destination.

Retention removes only recognized snapshots belonging to that installation, after a new backup and any due restore check succeed. A failed or interrupted restore check forces another check before retention can resume, even when the previous successful check was recent. It preserves the newly verified snapshot even if the system clock moves backwards. Unrelated folders and incomplete records are not pruned. An interrupted operation is reported after restart, and its recorded temporary copy is cleaned up before subsequent work. Failed cleanup remains recorded so the next maintenance attempt can retry it.

A periodic restore check verifies the saved archive, extracts it into a temporary directory at the backup destination, checks the restored artifact files, and boots the API in a separate process. It checks database and storage health and confirms that application writes are disabled. It opens no network listener and starts no conversion, extraction, MCP, or webhook workers. The API boot timeout defaults to 120 seconds and can be changed with `SPECTARR_BACKUP_RESTORE_TIMEOUT_SECONDS`.

## Recover or verify independently

A managed snapshot folder contains:

```text
snapshot.tar
SHA256SUMS
backup.json
IMAGE          (when a verifier image was configured)
```

Use the existing scripts against that folder:

```bash
scripts/verify-backup.sh /mnt/lab-backups/spectarr/spectarr-INSTANCE_ID/backup-TIMESTAMP-ID
scripts/restore-test.sh /mnt/lab-backups/spectarr/spectarr-INSTANCE_ID/backup-TIMESTAMP-ID /srv/spectarr-restore-test
```

The scripts accept these snapshots and the earlier manual backup formats. Set `SPECTARR_BACKUP_IMAGE` to a compatible Spectarr image or digest when the original installation is unavailable. Configuring this value in the backup override also records it in new snapshots. An image ID for a local source build may be available only on the original Docker host.

The standalone restore script starts an independent container in restore verification mode. See [reliability and recovery](reliability-and-recovery.md) for the full recovery procedure. Backups created through the older manual shell command are verified by those tools but are not enrolled in managed scheduling or retention.

## Validation

The integration passed 122 backend tests with 87.05% coverage, 69 dashboard tests, lint, type checks, and a production build. The isolated container rehearsal passed all three browser workflows, including dashboard backup creation and a restored API check. Tests cover retention after failed manual restore checks, cleanup retries, destination identity changes, interrupted temporary copies, scheduled catch-up, authorization, and later archive corruption. All validation used temporary synthetic data.
