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
  [ -z "$pids" ] || kill $pids
}

load_environment
api_port="${API_PORT:-8000}"
web_port="${WEB_PORT:-5173}"
"$project_root/scripts/setup_database.sh"
free_port "$api_port"
free_port "$web_port"

api_pid=""
web_pid=""
cleanup() {
  [ -z "$api_pid" ] || kill "$api_pid" 2>/dev/null || true
  [ -z "$web_pid" ] || kill "$web_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

"${UVICORN_BIN:-$project_root/api/.venv/bin/uvicorn}" knowledge_browser.main:app --reload --app-dir "$project_root/api/src" --host 127.0.0.1 --port "$api_port" &
api_pid=$!
(
  cd "$project_root/web"
  exec "${VITE_BIN:-./node_modules/.bin/vite}" --host 127.0.0.1 --port "$web_port"
) &
web_pid=$!

echo "API: http://127.0.0.1:$api_port"
echo "Web: http://127.0.0.1:$web_port"
wait "$api_pid" "$web_pid"
