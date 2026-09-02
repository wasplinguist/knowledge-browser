from pathlib import Path

import psycopg
import pytest


SCHEMAS = (
    Path(__file__).parents[2] / "db" / "init" / "001_schema.sql",
    Path(__file__).parents[2] / "db" / "init" / "002_bulk_import.sql",
)

pytestmark = pytest.mark.integration


def test_production_schema_has_required_tables(prepared_test_database):
    assert all(schema.is_file() for schema in SCHEMAS)
    with psycopg.connect(prepared_test_database) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
    assert {
        "users",
        "groups",
        "permission_sets",
        "documents",
        "chunks",
        "sentences",
        "search_events",
        "search_clicks",
        "bulk_import_runs",
        "bulk_import_progress",
        "bulk_embedding_cache",
    } <= tables
