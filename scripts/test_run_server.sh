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
  [ -z "${old_listener:-}" ] || kill "$old_listener" 2>/dev/null || true
  if [ "$had_env" -eq 1 ]; then cp "$env_backup" "$env_file"; else rm -f "$env_file"; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

log="$tmp/log"
old_listener=""
sleep 30 & old_listener=$!
disown "$old_listener" || true
cat >"$tmp/lsof" <<'EOF'
#!/usr/bin/env bash
[ "$*" = "-tiTCP:19080 -sTCP:LISTEN" ] && printf '%s\n' "$OLD_LISTENER"
EOF
cat >"$tmp/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" pg_isready "*) exit 0 ;;
  *" -tAc "*) echo 11 ;;
esac
EOF
cat >"$tmp/python" <<'EOF'
#!/usr/bin/env bash
printf 'python DATABASE_URL=%s %s\n' "${DATABASE_URL:-}" "$*" >>"$FAKE_LOG"
EOF
cat >"$tmp/server" <<'EOF'
#!/usr/bin/env bash
printf 'server DATABASE_URL=%s API_PORT=%s WEB_PORT=%s %s\n' "${DATABASE_URL:-}" "${API_PORT:-}" "${WEB_PORT:-}" "$*" >>"$FAKE_LOG"
trap 'printf "server-cleanup\n" >>"$FAKE_LOG"; exit 0' TERM INT
while :; do sleep 1; done
EOF
chmod +x "$tmp/lsof" "$tmp/docker" "$tmp/python" "$tmp/server"

printf 'DATABASE_URL=from-dotenv\nAPI_PORT=1\nWEB_PORT=2\nOPENAI_API_KEY=from-dotenv\n' >"$env_file"
FAKE_LOG="$log" OLD_LISTENER="$old_listener" PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  UVICORN_BIN="$tmp/server" VITE_BIN="$tmp/server" DATABASE_URL=from-process \
  API_PORT=19080 WEB_PORT=19573 OPENAI_API_KEY=from-process bash "$root/run_server.sh" >"$tmp/out" 2>&1 &
runner=$!
for _ in $(seq 1 50); do
  grep -q '^server ' "$log" 2>/dev/null && break
  sleep 0.05
done
grep -F 'API: http://127.0.0.1:19080' "$tmp/out" >/dev/null
grep -F 'Web: http://127.0.0.1:19573' "$tmp/out" >/dev/null
grep -F 'server DATABASE_URL=from-process API_PORT=19080 WEB_PORT=19573 knowledge_browser.main:app --reload --app-dir ' "$log" >/dev/null
grep -F 'server DATABASE_URL=from-process API_PORT=19080 WEB_PORT=19573 --host 127.0.0.1 --port 19573' "$log" >/dev/null
if kill -0 "$old_listener" 2>/dev/null; then
  echo 'configured API port listener was not stopped' >&2
  exit 1
fi
kill -TERM "$runner"
wait "$runner"
test "$(grep -c '^server-cleanup$' "$log")" = 2

echo 'run server tests passed'
