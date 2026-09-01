#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")" && pwd)"

load_environment() {
  [ -f "$project_root/.env" ] || return 0
  local line
  local saved_exports=()
  while IFS= read -r line; do saved_exports+=("$line"); done < <(export -p)
  set -a
  # shellcheck disable=SC1091
  source "$project_root/.env"
  set +a
  for line in "${saved_exports[@]}"; do eval "export${line#declare -x}"; done
}

free_port() {
  local port="$1" pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN || true)"
  [ -z "$pids" ] && return 0
  kill $pids
  for _ in $(seq 1 "${PORT_RELEASE_ATTEMPTS:-30}"); do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN || true)"
    [ -z "$pids" ] && return 0
    sleep "${PORT_RELEASE_SLEEP:-0.1}"
  done
  echo "port $port is still occupied after TERM" >&2
  return 1
}

load_environment
api_port="${API_PORT:-8000}"
web_port="${WEB_PORT:-5173}"
"$project_root/scripts/setup_database.sh"
free_port "$api_port"
free_port "$web_port"

api_pid=""
web_pid=""
child_status_dir="$(mktemp -d)"
first_status_path="$child_status_dir/first"
cleanup() {
  [ -z "$api_pid" ] || kill "$api_pid" 2>/dev/null || true
  [ -z "$web_pid" ] || kill "$web_pid" 2>/dev/null || true
  [ -z "$api_pid" ] || wait "$api_pid" 2>/dev/null || true
  [ -z "$web_pid" ] || wait "$web_pid" 2>/dev/null || true
  rm -rf "$child_status_dir"
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

run_watched() {
  local name="$1" child_pid="" status
  shift
  trap '[ -z "$child_pid" ] || kill "$child_pid" 2>/dev/null || true; [ -z "$child_pid" ] || wait "$child_pid" 2>/dev/null || true; exit 143' INT TERM
  "$@" &
  child_pid=$!
  set +e
  wait "$child_pid"
  status=$?
  set -e
  trap - INT TERM
  if mkdir "$child_status_dir/claimed" 2>/dev/null; then
    printf '%s %s\n' "$name" "$status" >"$first_status_path"
  fi
  return "$status"
}

run_watched api "${UVICORN_BIN:-$project_root/api/.venv/bin/uvicorn}" knowledge_browser.main:app --reload --app-dir "$project_root/api/src" --host 127.0.0.1 --port "$api_port" &
api_pid=$!
(
  cd "$project_root/web"
  run_watched web "${VITE_BIN:-./node_modules/.bin/vite}" --host 127.0.0.1 --port "$web_port"
) &
web_pid=$!

echo "API: http://127.0.0.1:$api_port"
echo "Web: http://127.0.0.1:$web_port"
while [ ! -s "$first_status_path" ]; do
  sleep "${RUN_SERVER_CHILD_POLL_SLEEP:-0.05}"
done
read -r first_service first_status <"$first_status_path"
exit "$first_status"
