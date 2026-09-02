#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"

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

explicit_database_url=0
[ -z "${DATABASE_URL:-}" ] || explicit_database_url=1
load_environment
[ -z "${DATABASE_URL:-}" ] || explicit_database_url=1
python_bin="${PYTHON_BIN:-$project_root/api/.venv/bin/python}"
table_count_sql="SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('users', 'groups', 'group_memberships', 'permission_sets', 'permission_set_users', 'permission_set_groups', 'documents', 'chunks', 'sentences', 'search_events', 'search_clicks', 'bulk_import_runs', 'bulk_import_progress', 'bulk_embedding_cache')"

if ! database_url="$("$python_bin" -c 'from knowledge_browser.config import database_url; print(database_url())' 2>/dev/null)"; then
  echo "database configuration failed; check database settings" >&2
  exit 1
fi
export DATABASE_URL="$database_url"

if [ "$explicit_database_url" -eq 0 ]; then docker compose up -d db; fi
ready=0
for _ in $(seq 1 30); do
  if "$python_bin" -c '
import os
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SELECT 1")
' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep "${SETUP_DATABASE_READY_SLEEP:-1}"
done
if [ "$ready" -ne 1 ]; then
  echo "database did not become ready; check database settings and database service logs" >&2
  exit 1
fi

if ! table_count="$("$python_bin" -c '
import os
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print(conn.execute("'"$table_count_sql"'").fetchone()[0])
' 2>/dev/null)"; then
  echo "database schema inspection failed; check database access" >&2
  exit 1
fi
case "$table_count" in
  0)
    if ! "$python_bin" -c '
import os
from pathlib import Path
import sys
import psycopg
schema_sql = "\n".join(Path(path).read_text() for path in sys.argv[1:])
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute(schema_sql)
    conn.execute("DROP INDEX IF EXISTS public.chunks_fts_idx")
    conn.execute("DROP INDEX IF EXISTS public.sentences_embedding_idx")
' "$project_root/db/init/001_schema.sql" "$project_root/db/init/002_bulk_import.sql" >/dev/null 2>&1; then
      echo "database schema setup failed; check database access and Compose logs" >&2
      exit 1
    fi
    ;;
  14) ;;
  *)
    echo "database is partially initialized; refusing setup" >&2
    exit 1
    ;;
esac

"$python_bin" -m knowledge_browser.bulk_cli run --data "$project_root/data/redwood"
"$python_bin" -m knowledge_browser.db_compat
