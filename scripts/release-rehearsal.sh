#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
version=$(tr -d '[:space:]' < "$repo_root/VERSION")
compose_file="$repo_root/release/compose.yaml"
if [[ ! -f "$compose_file" ]]
then
  compose_file="$repo_root/compose.yaml"
fi
rehearsal_root=${SPECTARR_REHEARSAL_RESUME_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/spectarr-release-rehearsal.XXXXXX")}
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
  "${compose[@]}" logs --no-color > "$rehearsal_root/container.log" 2>&1 || true
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
if [[ -n ${SPECTARR_REHEARSAL_RESUME_DIR:-} ]]
then
  smoke_password=$(cat "$rehearsal_root/.smoke-password")
else
  smoke_password=$(openssl rand -hex 24)
fi
(umask 077
  printf '%s\n' "$smoke_password" > "$rehearsal_root/.smoke-password"
)

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
  echo "SPECTARR_IMPORT_DIR=${SPECTARR_ACCEPTANCE_IMPORT_DIR:-$rehearsal_root/imports}"
  echo "SPECTARR_AUTH_MODE=password"
  echo "SPECTARR_ALLOW_REMOTE_NO_AUTH=false"
  echo "SPECTARR_MCP_ALLOW_WRITES=false"
  echo "SPECTARR_WEBHOOK_ALLOW_HTTP=true"
  echo "SPECTARR_WEBHOOK_ALLOW_PRIVATE_NETWORKS=true"
} > "$env_file"

compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file")
"${compose[@]}" config --quiet
if [[ ${SPECTARR_REHEARSAL_PULL:-true} == "true" ]]
then
  "${compose[@]}" pull
fi
"${compose[@]}" up -d
rehearsal_image=$("${compose[@]}" images -q spectarr)

SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_MCP_URL="http://127.0.0.1:$mcp_port/mcp" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/smoke_test.py"

if [[ -n ${SPECTARR_ACCEPTANCE_IMPORT_DIR:-} ]]
then
  export SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1"
  export SPECTARR_SMOKE_MCP_URL="http://127.0.0.1:$mcp_port/mcp"
  export SPECTARR_SMOKE_PASSWORD="$smoke_password"
  vendor_resume=()
  if [[ -n ${SPECTARR_ACCEPTANCE_PROJECT_ID:-} ]]
  then
    vendor_resume+=(--resume-project "$SPECTARR_ACCEPTANCE_PROJECT_ID")
  fi
  python3 "$repo_root/scripts/vendor-acceptance.py" "${vendor_resume[@]}" \
    --thermo /imports/angiotensin.raw --large-raw /imports/large-vendor.raw \
    --bruker /imports/example-dda.d --output "$rehearsal_root/vendor.json"
  python3 "$repo_root/scripts/agent-acceptance.py" \
    --vendor-results "$rehearsal_root/vendor.json" \
    --library-root "$rehearsal_root/data/storage/library" \
    --rename --output "$rehearsal_root/agent-before-restart.json"
  python3 "$repo_root/scripts/scientific-acceptance.py" \
    --vendor-results "$rehearsal_root/vendor.json" \
    --library-root "$rehearsal_root/data/storage/library" \
    --output "$rehearsal_root/scientific.json"
fi

if "${compose[@]}" exec -T spectarr python -c $'import importlib.util\nraise SystemExit(0 if importlib.util.find_spec("spectarr.library_publication") else 1)'
then
  "${compose[@]}" exec -T --user "$(id -u):$(id -g)" \
    -e SPECTARR_ACCEPTANCE_TEST=1 spectarr python - < "$repo_root/scripts/maintenance-acceptance.py"
else
  echo "Legacy image has no publication journal. Maintenance gate does not apply."
fi

soak_state="$rehearsal_root/sqlite-soak.json"
SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/sqlite-soak.py" enqueue "$soak_state"
"${compose[@]}" restart spectarr
SPECTARR_SMOKE_URL="http://127.0.0.1:$dashboard_port/api/v1" \
SPECTARR_SMOKE_PASSWORD="$smoke_password" \
python3 "$repo_root/scripts/sqlite-soak.py" verify "$soak_state"

if [[ -f "$rehearsal_root/vendor.json" ]]
then
  python3 "$repo_root/scripts/agent-acceptance.py" \
    --vendor-results "$rehearsal_root/vendor.json" \
    --library-root "$rehearsal_root/data/storage/library" \
    --output "$rehearsal_root/agent-after-restart.json"
fi

export SPECTARR_COMPOSE_FILE="$compose_file"
export SPECTARR_ENV_FILE="$env_file"
export SPECTARR_COMPOSE_PROJECT_NAME="$project_name"
export SPECTARR_DATA_DIR="$rehearsal_root/data"
"$repo_root/scripts/backup.sh" "$backup_root"
backup_dir=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | head -1)
"$repo_root/scripts/verify-backup.sh" "$backup_dir"
"$repo_root/scripts/restore-test.sh" "$backup_dir" "$restore_root"

echo "Release rehearsal passed for Spectarr $version"
