#!/usr/bin/env bash
set -euo pipefail

# Break caught: setup must not import until a fresh schema is applied, and
# must stop safely for partial schemas or unavailable PostgreSQL.
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
  if [ "$had_env" -eq 1 ]; then cp "$env_backup" "$env_file"; else rm -f "$env_file"; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

log="$tmp/log"
cat >"$tmp/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_LOG"
[ "$1 $2" != "compose exec" ] || {
  echo 'database operations must use DATABASE_URL, not Compose defaults' >&2
  exit 88
}
EOF
cat >"$tmp/python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *"knowledge_browser.config"*)
    [ "${FAKE_FAILURE:-}" != config ] || { printf '%s\n' "$DATABASE_URL" >&2; exit 7; }
    printf '%s\n' "${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/knowledge_search}"
    exit 0
    ;;
  *"SELECT 1"*)
    printf 'ready DATABASE_URL=%s\n' "${DATABASE_URL:-}" >>"$FAKE_LOG"
    [ "${FAKE_FAILURE:-}" != ready ] || { printf '%s\n' "$DATABASE_URL" >&2; exit 7; }
    [ "${FAKE_READY:-yes}" = yes ] || exit 1
    exit 0
    ;;
  *"pg_tables"*)
    printf 'table-count DATABASE_URL=%s\n' "${DATABASE_URL:-}" >>"$FAKE_LOG"
    [ "${FAKE_FAILURE:-}" != table-count ] || { printf '%s\n' "$DATABASE_URL" >&2; exit 7; }
    printf '%s\n' "${FAKE_TABLES:-0}"
    exit 0
    ;;
  *"schema_sql"*)
    printf 'schema DATABASE_URL=%s\n' "${DATABASE_URL:-}" >>"$FAKE_LOG"
    [ "${FAKE_FAILURE:-}" != schema ] || { printf '%s\n' "$DATABASE_URL" >&2; exit 7; }
    exit 0
    ;;
  *" knowledge_browser.bulk_cli run "*)
    [ "${FAKE_IMPORT:-ok}" = ok ] || { echo 'import failed' >&2; exit 9; }
    ;;
  *" knowledge_browser.db_compat "*)
    [ "${FAKE_COMPAT:-ok}" = ok ] || { echo 'compatibility check failed: database connection unavailable' >&2; exit 8; }
    ;;
esac
printf 'python DATABASE_URL=%s %s\n' "${DATABASE_URL:-}" "$*" >>"$FAKE_LOG"
EOF
chmod +x "$tmp/docker" "$tmp/python"

assert_order() {
  local database_url="$1" expected actual
  expected="ready DATABASE_URL=$database_url"$'\n'"table-count DATABASE_URL=$database_url"$'\n'"schema DATABASE_URL=$database_url"$'\n'"python DATABASE_URL=$database_url -m knowledge_browser.bulk_cli run --data "
  actual="$(sed -n '1,4p' "$log" | sed "4s|$root/data/redwood|data/redwood|")"
  expected+="data/redwood"
  [ "$actual" = "$expected" ] || { printf 'unexpected explicit-url setup order:\n%s\n' "$actual" >&2; return 1; }
  ! grep -q '^compose up -d db$' "$log"
}

assert_default_order() {
  local expected actual
  expected=$'compose up -d db\nready DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_search\ntable-count DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_search\nschema DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_search'
  actual="$(sed -n '1,4p' "$log")"
  [ "$actual" = "$expected" ] || { printf 'unexpected default-url setup order:\n%s\n' "$actual" >&2; return 1; }
}

printf 'DATABASE_URL=from-dotenv\n' >"$env_file"
FAKE_LOG="$log" DATABASE_URL=from-process PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  SETUP_DATABASE_READY_SLEEP=0 bash "$root/scripts/setup_database.sh"
assert_order from-process
grep -Fx "python DATABASE_URL=from-process -m knowledge_browser.bulk_cli run --data $root/data/redwood" "$log" >/dev/null
grep -Fx "python DATABASE_URL=from-process -m knowledge_browser.db_compat" "$log" >/dev/null

: >"$log"
env -u DATABASE_URL FAKE_LOG="$log" PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  SETUP_DATABASE_READY_SLEEP=0 bash "$root/scripts/setup_database.sh"
assert_order from-dotenv
grep -Fx "python DATABASE_URL=from-dotenv -m knowledge_browser.db_compat" "$log" >/dev/null

: >"$log"
rm "$env_file"
FAKE_LOG="$log" PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" SETUP_DATABASE_READY_SLEEP=0 \
  bash "$root/scripts/setup_database.sh"
assert_default_order

: >"$log"
FAKE_LOG="$log" FAKE_TABLES=14 PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh"
grep -q '^schema ' "$log" && { echo 'complete schema was reapplied' >&2; exit 1; }
grep -q 'knowledge_browser.bulk_cli run' "$log"

if FAKE_LOG="$log" FAKE_TABLES=3 PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
  echo 'partial schema was accepted' >&2
  exit 1
fi
grep -F 'partially initialized' "$tmp/out" >/dev/null

if FAKE_LOG="$log" FAKE_READY=no PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  SETUP_DATABASE_READY_SLEEP=0 bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
  echo 'unready database was accepted' >&2
  exit 1
fi
grep -F 'did not become ready' "$tmp/out" >/dev/null

sentinel_url='postgresql://setup:SENTINEL_PASSWORD@db.invalid/knowledge'
for failure in config ready table-count schema; do
  : >"$log"
  if FAKE_LOG="$log" FAKE_FAILURE="$failure" FAKE_TABLES=0 DATABASE_URL="$sentinel_url" \
    PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" SETUP_DATABASE_READY_SLEEP=0 \
    bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
    echo "$failure failure was accepted" >&2
    exit 1
  fi
  if grep -F 'SENTINEL_PASSWORD' "$tmp/out" >/dev/null; then
    echo "$failure failure exposed the database password" >&2
    exit 1
  fi
  case "$failure" in
    config) expected='database configuration failed; check database settings' ;;
    ready) expected='database did not become ready; check database settings and database service logs' ;;
    table-count) expected='database schema inspection failed; check database access' ;;
    schema) expected='database schema setup failed; check database access and Compose logs' ;;
  esac
  grep -F "$expected" "$tmp/out" >/dev/null
done

: >"$log"
if FAKE_LOG="$log" FAKE_IMPORT=fail PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
  echo 'import failure was accepted' >&2
  exit 1
fi
grep -Fx 'import failed' "$tmp/out" >/dev/null
! grep -q 'knowledge_browser.db_compat' "$log"

: >"$log"
if FAKE_LOG="$log" FAKE_TABLES=14 FAKE_COMPAT=fail PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
  echo 'compatibility failure was accepted' >&2
  exit 1
fi
grep -Fx 'compatibility check failed: database connection unavailable' "$tmp/out" >/dev/null

echo 'setup database tests passed'
