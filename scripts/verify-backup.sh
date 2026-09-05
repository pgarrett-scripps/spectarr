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
)
image=${SPECTARR_BACKUP_IMAGE:-}
if [[ -z "$image" && -f "$backup_dir/IMAGE" ]]
then
  image=$(cat "$backup_dir/IMAGE")
fi
if [[ -z "$image" ]]
then
  image=$("${compose[@]}" images -q spectarr 2>/dev/null || true)
  if [[ -z "$image" ]]
  then
    image=$("${compose[@]}" config --images | head -1)
  fi
fi
if [[ -z "$image" ]]
then
  echo "Set SPECTARR_BACKUP_IMAGE to an image containing the backup verifier" >&2
  exit 1
fi
if [[ -f "$backup_dir/snapshot.tar" ]]
then
  docker run --rm -i --network none --entrypoint spectarr-backup "$image" verify-set < "$backup_dir/snapshot.tar"
else
  tar --create --file - --directory "$backup_dir" database.sqlite3 storage.tar |
    docker run --rm -i --network none --entrypoint spectarr-backup "$image" verify-set
fi

echo "Backup verified: $backup_dir"
