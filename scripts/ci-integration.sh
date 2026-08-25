#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$repo_root/.env.ci"
project_name="spectarr-ci-${GITHUB_RUN_ID:-$$}"
backup_work=$(mktemp -d "${TMPDIR:-/tmp}/spectarr-ci-backup.XXXXXX")

read -r api_port dashboard_port mcp_port < <(
  python3 -c $'import socket\nsockets = [socket.socket() for _ in range(3)]\n[s.bind(("127.0.0.1", 0)) for s in sockets]\nprint(*(s.getsockname()[1] for s in sockets))\n[s.close() for s in sockets]'
)

cleanup() {
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$repo_root/compose.yaml" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$env_file"
  rm -rf "$backup_work"
}
trap cleanup EXIT

show_failure() {
  echo "Integration stack failed. Container status and API logs follow." >&2
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$repo_root/compose.yaml" ps >&2 || true
  docker compose --project-name "$project_name" --env-file "$env_file" -f "$repo_root/compose.yaml" logs api postgres >&2 || true
}
trap show_failure ERR

if [[ -e "$env_file" ]]
then
  echo "Refusing to replace existing integration environment: $env_file" >&2
  exit 1
fi

mkdir -p "$repo_root/data" "$repo_root/imports"
database_password=$(openssl rand -hex 32)
{
  echo "SPECTARR_SECRET_KEY=$(openssl rand -hex 32)"
  echo "SPECTARR_WORKER_TOKEN=$(openssl rand -hex 32)"
  echo "POSTGRES_PASSWORD=$database_password"
  echo "SPECTARR_DATABASE_URL=postgresql+psycopg://spectarr:$database_password@postgres:5432/spectarr"
  echo "SPECTARR_DOCKER_DATA_ROOT=$repo_root/data"
  echo "SPECTARR_UID=$(id -u)"
  echo "SPECTARR_GID=$(id -g)"
  echo "SPECTARR_BIND_ADDRESS=127.0.0.1"
  echo "SPECTARR_API_PORT=$api_port"
  echo "SPECTARR_DASHBOARD_PORT=$dashboard_port"
  echo "SPECTARR_MCP_PORT=$mcp_port"
} > "$env_file"

compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$repo_root/compose.yaml")
"${compose[@]}" config --quiet
"${compose[@]}" up -d --build
SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" python3 "$repo_root/scripts/smoke_test.py"

export SPECTARR_COMPOSE_FILE="$repo_root/compose.yaml"
export SPECTARR_ENV_FILE="$env_file"
export SPECTARR_COMPOSE_PROJECT_NAME="$project_name"
export SPECTARR_DATA_DIR="$repo_root/data"
"$repo_root/scripts/backup.sh" "$backup_work"
backup_dir=$(find "$backup_work" -mindepth 1 -maxdepth 1 -type d -name 'spectarr-*' | head -1)
"$repo_root/scripts/verify-backup.sh" "$backup_dir"
"$repo_root/scripts/restore-test.sh" "$backup_dir" "$backup_work/restore" spectarr_ci_restore

if [[ ${SPECTARR_RUN_E2E:-false} == "true" ]]
then
  (
    cd "$repo_root/frontend"
    SPECTARR_E2E_URL="http://127.0.0.1:$dashboard_port" \
    SPECTARR_E2E_PASSWORD=release-rehearsal-admin-password \
    npm run test:e2e
  )
fi
