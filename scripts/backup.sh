#!/usr/bin/env bash
set -euo pipefail

backup_parent=${1:?Usage: scripts/backup.sh BACKUP_PARENT}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="${backup_parent%/}/spectarr-${timestamp}"
postgres_user=${POSTGRES_USER:-spectarr}
postgres_database=${POSTGRES_DB:-spectarr}
data_dir=${SPECTARR_DATA_DIR:-data}
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
"${compose[@]}" exec -T postgres pg_dump --username "$postgres_user" --dbname "$postgres_database" --format custom > "$backup_dir/database.dump"
tar --create --file "$backup_dir/storage.tar" --directory "$data_dir" storage
(
  cd "$backup_dir"
  sha256sum database.dump storage.tar > SHA256SUMS
)

echo "$backup_dir"
