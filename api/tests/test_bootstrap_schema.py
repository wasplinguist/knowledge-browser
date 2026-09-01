from pathlib import Path

import psycopg
import pytest


SCHEMA = Path(__file__).parents[2] / "db" / "init" / "001_schema.sql"

pytestmark = pytest.mark.integration


def test_production_schema_has_required_tables(prepared_test_database):
    assert SCHEMA.is_file()
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
    } <= tables
