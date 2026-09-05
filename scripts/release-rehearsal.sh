#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
version=$(tr -d '[:space:]' < "$repo_root/VERSION")
compose_file="$repo_root/release/compose.yaml"
if [[ ! -f "$compose_file" ]]
then
  compose_file="$repo_root/compose.yaml"
fi
rehearsal_root=$(mktemp -d "${TMPDIR:-/tmp}/spectarr-release-rehearsal.XXXXXX")
project_name="spectarr-rehearsal-$$"
env_file="$rehearsal_root/.env"
backup_root="$rehearsal_root/backups"
restore_root="$rehearsal_root/restore"
rehearsal_image=""

if [[ -z ${SPECTARR_REHEARSAL_PORT:-} || -z ${SPECTARR_REHEARSAL_MCP_PORT:-} ]]
then
  read -r allocated_dashboard_port allocated_mcp_port < <(
    python3 -c $'import socket\nsockets = [socket.socket() for _ in range(2)]\n[s.bind(("127.0.0.1", 0)) for s in sockets]\nprint(*(s.getsockname()[1] for s in sockets))\n[s.close() for s in sockets]'
  )
fi
dashboard_port=${SPECTARR_REHEARSAL_PORT:-$allocated_dashboard_port}
mcp_port=${SPECTARR_REHEARSAL_MCP_PORT:-$allocated_mcp_port}

cleanup() {
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" exec -T --user root spectarr /bin/chown -R "$(id -u):$(id -g)" /data >/dev/null 2>&1 || true
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" stop spectarr >/dev/null 2>&1 || true
  if [[ -n "$rehearsal_image" ]]
  then
    docker run --rm --user root --entrypoint /bin/chown \
      --mount "type=bind,source=$rehearsal_root/data,target=/data" \
      "$rehearsal_image" -R "$(id -u):$(id -g)" /data >/dev/null 2>&1 || true
  fi
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
  if [[ ${SPECTARR_KEEP_REHEARSAL:-false} != "true" ]]
  then
    rm -rf "$rehearsal_root"
  else
    echo "Rehearsal files retained at $rehearsal_root"
  fi
}
trap cleanup EXIT

mkdir -p "$rehearsal_root/data" "$rehearsal_root/imports" "$backup_root"
smoke_password=$(openssl rand -hex 24)

{
  echo "SPECTARR_VERSION=$version"
  echo "SPECTARR_IMAGE_REF=${SPECTARR_IMAGE_REF:-}"
  echo "SPECTARR_IMAGE=${SPECTARR_IMAGE:-ghcr.io/pgarrett-scripps/spectarr}"
  echo "SPECTARR_BIND_ADDRESS=127.0.0.1"
  echo "SPECTARR_PORT=$dashboard_port"
  echo "SPECTARR_MCP_PORT=$mcp_port"
  echo "SPECTARR_JOB_LEASE_SECONDS=30"
  echo "SPECTARR_UID=$(id -u)"
  echo "SPECTARR_GID=$(id -g)"
  echo "SPECTARR_DATA_DIR=$rehearsal_root/data"
  echo "SPECTARR_IMPORT_DIR=$rehearsal_root/imports"
  echo "SPECTARR_AUTH_MODE=password"
  echo "SPECTARR_ALLOW_REMOTE_NO_AUTH=false"
  echo "SPECTARR_MCP_ALLOW_WRITES=false"
  echo "SPECTARR_WEBHOOK_ALLOW_HTTP=true"
  echo "SPECTARR_WEBHOOK_ALLOW_PRIVATE_NETWORKS=true"
} > "$env_file"

compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file")
"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d
rehearsal_image=$("${compose[@]}" images -q spectarr)

SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_MCP_URL="http://127.0.0.1:$mcp_port/mcp" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/smoke_test.py"

soak_state="$rehearsal_root/sqlite-soak.json"
SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/sqlite-soak.py" enqueue "$soak_state"
"${compose[@]}" restart spectarr
SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/sqlite-soak.py" verify "$soak_state"

export SPECTARR_COMPOSE_FILE="$compose_file"
export SPECTARR_ENV_FILE="$env_file"
export SPECTARR_COMPOSE_PROJECT_NAME="$project_name"
export SPECTARR_DATA_DIR="$rehearsal_root/data"
"$repo_root/scripts/backup.sh" "$backup_root"
backup_dir=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | head -1)
"$repo_root/scripts/verify-backup.sh" "$backup_dir"
"$repo_root/scripts/restore-test.sh" "$backup_dir" "$restore_root"

echo "Release rehearsal passed for Spectarr $version"
