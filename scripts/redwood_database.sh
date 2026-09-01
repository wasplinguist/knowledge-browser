#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
compose_project="knowledge-browser-redwood"

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

container_owner() {
  "$docker_bin" inspect \
    --format '{{ index .Config.Labels "com.docker.compose.project" }} {{ index .Config.Labels "com.docker.compose.service" }}' \
    knowledge-redwood-db 2>/dev/null
}

container_details() {
  "$docker_bin" inspect \
    --format '{{- $bindings := index .NetworkSettings.Ports "5432/tcp" -}}{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ .State.Running }}|{{ len $bindings }}{{ range $bindings }}|{{ .HostIp }}|{{ .HostPort }}{{ end }}' \
    knowledge-redwood-db 2>/dev/null
}

require_safe_container() {
  local owner
  if owner="$(container_owner)"; then
    [ "$owner" = "$compose_project redwood-db" ] || {
      echo "knowledge-redwood-db already exists outside this Compose project;" \
        "remove it explicitly before start." >&2
      return 1
    }
  fi
}

require_managed_container() {
  local owner details expected_port expected
  owner="$(container_owner)" || owner=""
  [ "$owner" = "$compose_project redwood-db" ] || {
    echo "knowledge-redwood-db is not managed by this Compose project;" \
      "run start or complete the explicit handoff first." >&2
    return 1
  }
  expected_port="${REDWOOD_POSTGRES_PORT:-5433}"
  expected="$compose_project|redwood-db|true|1|127.0.0.1|$expected_port"
  details="$(container_details)" || details=""
  [ "$details" = "$expected" ] || {
    echo "Redwood container check failed: reason=container_mismatch;" \
      "next_step=run start and check REDWOOD_POSTGRES_PORT" \
      "(expected 127.0.0.1:$expected_port)." >&2
    return 1
  }
}

load_environment
python_bin="${PYTHON_BIN:-$project_root/api/.venv/bin/python}"
if [ ! -x "$python_bin" ] && [ -z "${PYTHON_BIN:-}" ]; then
  common_dir="$(git -C "$project_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$common_dir" ]; then
    shared_python="$(dirname "$common_dir")/api/.venv/bin/python"
    [ ! -x "$shared_python" ] || python_bin="$shared_python"
  fi
fi
[ -x "$python_bin" ] || {
  echo "Redwood Python environment is missing; install the API dependencies." >&2
  exit 1
}
docker_bin="${DOCKER_BIN:-docker}"
export PYTHONPATH="$project_root/api/src${PYTHONPATH:+:$PYTHONPATH}"
command="${1:-}"
[ "$#" -eq 0 ] || shift

case "$command" in
  start)
    require_safe_container
    "$docker_bin" compose --project-directory "$project_root" \
      -p "$compose_project" --profile redwood up -d --wait --wait-timeout 60 redwood-db
    ;;
  stop)
    require_safe_container
    "$docker_bin" compose --project-directory "$project_root" \
      -p "$compose_project" --profile redwood stop redwood-db
    ;;
  validate|reset|run|status|verify)
    case "$command" in reset|run) require_managed_container ;; esac
    export POSTGRES_HOST="127.0.0.1"
    export POSTGRES_PORT="${REDWOOD_POSTGRES_PORT:-5433}"
    export POSTGRES_DB="knowledge_redwood"
    export POSTGRES_USER="${REDWOOD_POSTGRES_USER:-postgres}"
    export POSTGRES_PASSWORD="${REDWOOD_POSTGRES_PASSWORD:-postgres}"
    unset DATABASE_URL
    if ! DATABASE_URL="$("$python_bin" -c \
      'from knowledge_browser.config import database_url; print(database_url())' \
      2>/dev/null)"; then
      echo "Redwood database configuration failed." >&2
      exit 1
    fi
    export DATABASE_URL
    exec "$python_bin" -m knowledge_browser.bulk_cli "$command" "$@"
    ;;
  *)
    echo "Usage: $0 {start|validate|reset|run|status|verify|stop} [options]" >&2
    exit 2
    ;;
esac
