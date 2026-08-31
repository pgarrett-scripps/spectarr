#!/usr/bin/env bash
set -euo pipefail

backup_dir=${1:?Usage: scripts/restore-test.sh BACKUP_DIRECTORY RESTORE_DIRECTORY}
restore_dir=${2:?Usage: scripts/restore-test.sh BACKUP_DIRECTORY RESTORE_DIRECTORY}
script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_root/.." && pwd)
compose_file=${SPECTARR_COMPOSE_FILE:-$repo_root/compose.yaml}
restore_project="${SPECTARR_COMPOSE_PROJECT_NAME:-spectarr}-restore-$$"
restore_image=""

if [[ -e "$restore_dir" ]]
then
  echo "Restore directory must not already exist: $restore_dir" >&2
  exit 1
fi

"$script_root/verify-backup.sh" "$backup_dir"
mkdir -p "$restore_dir"
tar --extract --file "$backup_dir/storage.tar" --directory "$restore_dir"
cp "$backup_dir/database.sqlite3" "$restore_dir/spectarr.db"
mkdir -p "$restore_dir/imports"
restore_dir=$(cd "$restore_dir" && pwd)
restore_env="$restore_dir/.restore-test.env"
read -r dashboard_port mcp_port < <(
  python3 -c $'import socket\nsockets = [socket.socket() for _ in range(2)]\n[s.bind(("127.0.0.1", 0)) for s in sockets]\nprint(*(s.getsockname()[1] for s in sockets))\n[s.close() for s in sockets]'
)

{
  echo "SPECTARR_BIND_ADDRESS=127.0.0.1"
  echo "SPECTARR_PORT=$dashboard_port"
  echo "SPECTARR_MCP_PORT=$mcp_port"
  echo "SPECTARR_UID=$(id -u)"
  echo "SPECTARR_GID=$(id -g)"
  echo "SPECTARR_DATA_DIR=$restore_dir"
  echo "SPECTARR_IMPORT_DIR=$restore_dir/imports"
  echo "SPECTARR_IMPORTS_DIR=$restore_dir/imports"
  echo "SPECTARR_DOCKER_DATA_ROOT=$restore_dir"
  echo "SPECTARR_SECRET_KEY=$(openssl rand -hex 32)"
  echo "SPECTARR_WORKER_TOKEN=$(openssl rand -hex 32)"
  echo "SPECTARR_AUTH_MODE=password"
  echo "SPECTARR_ALLOW_REMOTE_NO_AUTH=false"
} > "$restore_env"

restore_compose=(docker compose --project-name "$restore_project")
if [[ -n ${SPECTARR_ENV_FILE:-} ]]
then
  restore_compose+=(--env-file "$SPECTARR_ENV_FILE")
fi
restore_compose+=(--env-file "$restore_env" -f "$compose_file")

cleanup() {
  "${restore_compose[@]}" exec -T --user root spectarr /bin/chown -R "$(id -u):$(id -g)" /data > /dev/null 2>&1 || true
  "${restore_compose[@]}" stop spectarr > /dev/null 2>&1 || true
  if [[ -n "$restore_image" ]]
  then
    docker run --rm --user root --entrypoint /bin/chown \
      --mount "type=bind,source=$restore_dir,target=/data" \
      "$restore_image" -R "$(id -u):$(id -g)" /data > /dev/null 2>&1 || true
  fi
  "${restore_compose[@]}" down --volumes --remove-orphans > /dev/null 2>&1 || true
  rm -f "$restore_env"
}
trap cleanup EXIT

"${restore_compose[@]}" config --quiet
"${restore_compose[@]}" up -d --build
restore_image=$("${restore_compose[@]}" images -q spectarr)
healthy=false
for _attempt in {1..120}
do
  if "${restore_compose[@]}" exec -T spectarr python -c $'import urllib.request\nurllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)' > /dev/null 2>&1
  then
    healthy=true
    break
  fi
  sleep 1
done
if [[ "$healthy" != "true" ]]
then
  "${restore_compose[@]}" logs spectarr >&2 || true
  echo "Restored Spectarr instance did not become healthy" >&2
  exit 1
fi
"${restore_compose[@]}" exec -T spectarr python -c $'import json\nimport os\nimport urllib.request\nrequest = urllib.request.Request("http://127.0.0.1:8000/api/v1/system/health", headers={"X-Spectarr-Worker-Token": os.environ["SPECTARR_WORKER_TOKEN"]})\nwith urllib.request.urlopen(request, timeout=5) as response:\n    payload = json.load(response)\nif payload.get("database") != "ok" or payload.get("storage") != "ok":\n    raise SystemExit(f"Restored system health failed: {payload}")'

echo "Restore test started and validated an independent instance in $restore_dir"
