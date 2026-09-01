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

load_environment
python_bin="${PYTHON_BIN:-$project_root/api/.venv/bin/python}"
table_count_sql="SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('users', 'groups', 'group_memberships', 'permission_sets', 'permission_set_users', 'permission_set_groups', 'documents', 'chunks', 'sentences', 'search_events', 'search_clicks')"

database_url="$("$python_bin" -c 'from knowledge_browser.config import database_url; print(database_url())')"
export DATABASE_URL="$database_url"

docker compose up -d db
ready=0
for _ in $(seq 1 30); do
  if "$python_bin" -c '
import os
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SELECT 1")
'; then
    ready=1
    break
  fi
  sleep "${SETUP_DATABASE_READY_SLEEP:-1}"
done
if [ "$ready" -ne 1 ]; then
  echo "database did not become ready" >&2
  exit 1
fi

table_count="$("$python_bin" -c '
import os
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print(conn.execute("'"$table_count_sql"'").fetchone()[0])
' | tr -d '[:space:]')"
case "$table_count" in
  0)
    "$python_bin" -c '
import os
from pathlib import Path
import sys
import psycopg
schema_sql = Path(sys.argv[1]).read_text()
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute(schema_sql)
' "$project_root/db/init/001_schema.sql"
    ;;
  11) ;;
  *)
    echo "database is partially initialized; refusing setup" >&2
    exit 1
    ;;
esac

"$python_bin" -m knowledge_browser.bootstrap --data "$project_root/data/company"
"$python_bin" -m knowledge_browser.db_compat
