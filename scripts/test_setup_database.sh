#!/usr/bin/env bash
set -euo pipefail

# Break caught: setup must not bootstrap until a fresh schema is applied, and
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
if [ "$1 $2" = "compose exec" ]; then
  case " $* " in
    *" pg_isready "*)
      [ "${FAKE_READY:-yes}" = yes ] && exit 0
      exit 1
      ;;
    *" -tAc "*)
      printf '%s\n' "${FAKE_TABLES:-0}"
      ;;
    *" psql "*)
      cat >/dev/null
      printf 'schema-stdin\n' >>"$FAKE_LOG"
      ;;
  esac
fi
EOF
cat >"$tmp/python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'python DATABASE_URL=%s %s\n' "${DATABASE_URL:-}" "$*" >>"$FAKE_LOG"
case " $* " in
  *" knowledge_browser.bootstrap "*) [ "${FAKE_BOOTSTRAP:-ok}" = ok ] || exit 9 ;;
esac
EOF
chmod +x "$tmp/docker" "$tmp/python"

assert_order() {
  local expected actual
  expected=$'compose up -d db\ncompose exec -T db pg_isready -U postgres -d knowledge_search\ncompose exec -T db psql -U postgres -d knowledge_search -tAc SELECT count(*) FROM pg_tables WHERE schemaname = \'public\' AND tablename IN (\'users\', \'groups\', \'group_memberships\', \'permission_sets\', \'permission_set_users\', \'permission_set_groups\', \'documents\', \'chunks\', \'sentences\', \'search_events\', \'search_clicks\')\ncompose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d knowledge_search\nschema-stdin'
  actual="$(sed -n '1,5p' "$log")"
  [ "$actual" = "$expected" ] || { printf 'unexpected setup order:\n%s\n' "$actual" >&2; return 1; }
}

printf 'DATABASE_URL=from-dotenv\n' >"$env_file"
FAKE_LOG="$log" DATABASE_URL=from-process PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh"
assert_order
grep -Fx "python DATABASE_URL=from-process -m knowledge_browser.bootstrap --data $root/data/company" "$log" >/dev/null
grep -Fx "python DATABASE_URL=from-process -m knowledge_browser.db_compat" "$log" >/dev/null

: >"$log"
FAKE_LOG="$log" FAKE_TABLES=11 PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh"
grep -q 'schema-stdin' "$log" && { echo 'complete schema was reapplied' >&2; exit 1; }
grep -q 'knowledge_browser.bootstrap' "$log"

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

: >"$log"
if FAKE_LOG="$log" FAKE_BOOTSTRAP=fail PATH="$tmp:$PATH" PYTHON_BIN="$tmp/python" \
  bash "$root/scripts/setup_database.sh" >"$tmp/out" 2>&1; then
  echo 'bootstrap failure was accepted' >&2
  exit 1
fi
! grep -q 'knowledge_browser.db_compat' "$log"

echo 'setup database tests passed'
