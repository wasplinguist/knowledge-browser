#!/usr/bin/env bash
set -euo pipefail

# Break caught: the developer entry point must release the configured ports,
# export the effective environment, start both services, and clean them up.
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
env_file="$root/.env"
env_backup="$tmp/env-backup"
had_env=0
if [ -e "$env_file" ]; then
  cp "$env_file" "$env_backup"
  had_env=1
fi
cleanup() {
  [ -z "${case_runner:-}" ] || kill "$case_runner" 2>/dev/null || true
  [ -z "${old_listener:-}" ] || kill "$old_listener" 2>/dev/null || true
  [ -z "${old_web_listener:-}" ] || kill "$old_web_listener" 2>/dev/null || true
  [ -z "${stubborn_listener:-}" ] || kill -KILL "$stubborn_listener" 2>/dev/null || true
  if [ "$had_env" -eq 1 ]; then cp "$env_backup" "$env_file"; else rm -f "$env_file"; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

log="$tmp/log"
old_listener=""
old_web_listener=""
stubborn_listener=""
case_runner=""
choose_port() {
  python3 -c 'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()'
}
api_port="$(choose_port)"
web_port="$(choose_port)"
while [ "$web_port" = "$api_port" ]; do web_port="$(choose_port)"; done
sleep 30 & old_listener=$!
disown "$old_listener" || true
sleep 30 & old_web_listener=$!
disown "$old_web_listener" || true
cat >"$tmp/lsof" <<'EOF'
#!/usr/bin/env bash
if [ -n "${STUBBORN_LISTENER:-}" ]; then printf '%s\n' "$STUBBORN_LISTENER"; exit 0; fi
case "$*" in
  "-tiTCP:${API_PORT} -sTCP:LISTEN") target="$OLD_LISTENER" ;;
  "-tiTCP:${WEB_PORT} -sTCP:LISTEN") target="$OLD_WEB_LISTENER" ;;
  *) exit 0 ;;
esac
kill -0 "$target" 2>/dev/null && printf '%s\n' "$target"
EOF
cat >"$tmp/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[ "$1 $2" != "compose exec" ] || exit 88
EOF
cat >"$tmp/python" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" knowledge_browser.config "*) printf '%s\n' "${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/knowledge_search}" ;;
  *" SELECT 1 "*) exit 0 ;;
  *" pg_tables "*) echo 11 ;;
esac
printf 'python DATABASE_URL=%s %s\n' "${DATABASE_URL:-}" "$*" >>"$FAKE_LOG"
EOF
cat >"$tmp/server" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" knowledge_browser.main:app "*) service=api ;;
  *) service=web ;;
esac
printf 'server-%s DATABASE_URL=%s API_PORT=%s WEB_PORT=%s %s\n' "$service" "${DATABASE_URL:-}" "${API_PORT:-}" "${WEB_PORT:-}" "$*" >>"$FAKE_LOG"
trap 'printf "server-cleanup-%s\n" "$service" >>"$FAKE_LOG"; exit 0' TERM INT
if [ "${EXIT_SERVICE:-}" = "$service" ]; then
  sleep "${EXIT_DELAY:-0.05}"
  printf 'server-exit-%s-%s\n' "$service" "$EXIT_STATUS" >>"$FAKE_LOG"
  exit "$EXIT_STATUS"
fi
while :; do sleep 1; done
EOF
chmod +x "$tmp/lsof" "$tmp/docker" "$tmp/python" "$tmp/server"

printf 'DATABASE_URL=from-dotenv\nAPI_PORT=1\nWEB_PORT=2\nOPENAI_API_KEY=from-dotenv\n' >"$env_file"
FAKE_LOG="$log" OLD_LISTENER="$old_listener" OLD_WEB_LISTENER="$old_web_listener" API_PORT="$api_port" WEB_PORT="$web_port" PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  UVICORN_BIN="$tmp/server" VITE_BIN="$tmp/server" DATABASE_URL=from-process \
  OPENAI_API_KEY=from-process PORT_RELEASE_SLEEP=0 bash "$root/run_server.sh" >"$tmp/out" 2>&1 &
runner=$!
for _ in $(seq 1 50); do
  grep -q '^server-' "$log" 2>/dev/null && break
  sleep 0.05
done
grep -F "API: http://127.0.0.1:$api_port" "$tmp/out" >/dev/null
grep -F "Web: http://127.0.0.1:$web_port" "$tmp/out" >/dev/null
grep -F "server-api DATABASE_URL=from-process API_PORT=$api_port WEB_PORT=$web_port knowledge_browser.main:app --reload --app-dir " "$log" >/dev/null
grep -F "server-web DATABASE_URL=from-process API_PORT=$api_port WEB_PORT=$web_port --host 127.0.0.1 --port $web_port" "$log" >/dev/null
if kill -0 "$old_listener" 2>/dev/null; then
  echo 'configured API port listener was not stopped' >&2
  exit 1
fi
if kill -0 "$old_web_listener" 2>/dev/null; then
  echo 'configured web port listener was not stopped' >&2
  exit 1
fi
kill -TERM "$runner"
wait "$runner"
test "$(grep -c '^server-cleanup-' "$log")" = 2

assert_first_exit() {
  local service="$1" expected_status="$2" actual_status
  : >"$log"
  FAKE_LOG="$log" EXIT_SERVICE="$service" EXIT_STATUS="$expected_status" \
    PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" UVICORN_BIN="$tmp/server" \
    VITE_BIN="$tmp/server" DATABASE_URL=from-process API_PORT="$api_port" \
    WEB_PORT="$web_port" RUN_SERVER_CHILD_POLL_SLEEP=0.01 \
    bash "$root/run_server.sh" >"$tmp/out" 2>&1 &
  case_runner=$!
  for _ in $(seq 1 200); do
    kill -0 "$case_runner" 2>/dev/null || break
    sleep 0.01
  done
  if kill -0 "$case_runner" 2>/dev/null; then
    echo "$service-first runner did not exit promptly" >&2
    return 1
  fi
  set +e
  wait "$case_runner"
  actual_status=$?
  set -e
  case_runner=""
  [ "$actual_status" -eq "$expected_status" ] || {
    echo "$service-first status $actual_status, expected $expected_status" >&2
    return 1
  }
  grep -Fx "server-exit-$service-$expected_status" "$log" >/dev/null
  case "$service" in
    api) grep -Fx 'server-cleanup-web' "$log" >/dev/null ;;
    web) grep -Fx 'server-cleanup-api' "$log" >/dev/null ;;
  esac
}

assert_first_exit api 0
assert_first_exit api 23
assert_first_exit web 0
assert_first_exit web 37

bash -c 'trap "" TERM; while :; do sleep 1; done' & stubborn_listener=$!
disown "$stubborn_listener" || true
if FAKE_LOG="$log" STUBBORN_LISTENER="$stubborn_listener" PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  UVICORN_BIN="$tmp/server" VITE_BIN="$tmp/server" DATABASE_URL=from-process API_PORT="$api_port" WEB_PORT="$web_port" \
  PORT_RELEASE_ATTEMPTS=2 PORT_RELEASE_SLEEP=0 bash "$root/run_server.sh" >"$tmp/out" 2>&1; then
  echo 'still-occupied port was accepted' >&2
  exit 1
fi
grep -F "port $api_port is still occupied" "$tmp/out" >/dev/null

echo 'run server tests passed'
