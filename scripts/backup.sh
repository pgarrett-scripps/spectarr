#!/usr/bin/env bash
set -euo pipefail

backup_parent=${1:?Usage: scripts/backup.sh BACKUP_PARENT}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="${backup_parent%/}/spectarr-${timestamp}"
compose=(docker compose)
if [[ -n ${SPECTARR_COMPOSE_PROJECT_NAME:-} ]]
then
  compose+=(--project-name "$SPECTARR_COMPOSE_PROJECT_NAME")
fi
if [[ -n ${SPECTARR_ENV_FILE:-} ]]
then
  compose+=(--env-file "$SPECTARR_ENV_FILE")
fi
if [[ -n ${SPECTARR_COMPOSE_FILE:-} ]]
then
  compose+=(-f "$SPECTARR_COMPOSE_FILE")
fi

if [[ -e "$backup_dir" ]]
then
  echo "Backup target already exists: $backup_dir" >&2
  exit 1
fi

mkdir -p "$backup_dir"
"${compose[@]}" exec -T spectarr spectarr-backup create /data/spectarr.db > "$backup_dir/database.sqlite3"
"${compose[@]}" exec -T spectarr python -c $'import sys\nimport tarfile\nfrom pathlib import Path\nwith tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:\n    for name in ("storage", ".spectarr"):\n        path = Path("/data") / name\n        if path.exists():\n            archive.add(path, arcname=name)' > "$backup_dir/storage.tar"
(
  cd "$backup_dir"
  sha256sum database.sqlite3 storage.tar > SHA256SUMS
)

echo "$backup_dir"
