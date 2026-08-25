#!/usr/bin/env bash
set -euo pipefail

backup_dir=${1:?Usage: scripts/restore-test.sh BACKUP_DIRECTORY RESTORE_DIRECTORY [DATABASE_NAME]}
restore_dir=${2:?Usage: scripts/restore-test.sh BACKUP_DIRECTORY RESTORE_DIRECTORY [DATABASE_NAME]}
restore_database=${3:-spectarr_restore_test}
postgres_user=${POSTGRES_USER:-spectarr}
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

if [[ ! "$restore_database" =~ ^[a-zA-Z0-9_]+$ ]]
then
  echo "Database name may contain only letters, numbers, and underscores" >&2
  exit 1
fi

if [[ -e "$restore_dir" ]]
then
  echo "Restore directory must not already exist: $restore_dir" >&2
  exit 1
fi

scripts/verify-backup.sh "$backup_dir"

if "${compose[@]}" exec -T postgres psql --username "$postgres_user" --tuples-only --command "SELECT 1 FROM pg_database WHERE datname = '$restore_database'" | tr -d '[:space:]' | grep -q 1
then
  echo "Restore database already exists: $restore_database" >&2
  exit 1
fi

mkdir -p "$restore_dir"
tar --extract --file "$backup_dir/storage.tar" --directory "$restore_dir"
"${compose[@]}" exec -T postgres createdb --username "$postgres_user" "$restore_database"
"${compose[@]}" exec -T postgres pg_restore --username "$postgres_user" --dbname "$restore_database" < "$backup_dir/database.dump"

echo "Restore test completed in $restore_dir with database $restore_database"
