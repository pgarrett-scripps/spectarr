#!/usr/bin/env bash
set -euo pipefail

backup_dir=${1:?Usage: scripts/verify-backup.sh BACKUP_DIRECTORY}
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

(
  cd "$backup_dir"
  sha256sum --check SHA256SUMS
  tar --list --file storage.tar > /dev/null
)
"${compose[@]}" exec -T spectarr spectarr-backup verify < "$backup_dir/database.sqlite3" > /dev/null

echo "Backup verified: $backup_dir"
