import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import _seed_malformed_root_chain, _test_database_url
from knowledge_browser.db_compat import check_compatibility


pytestmark = pytest.mark.integration


def test_compatibility_reports_only_safe_aggregate_counts(db):
    report = check_compatibility(db)

    assert report.compatible is True
    assert report.issues == ()
    assert report.document_count == 8
    assert report.chunk_count == 8
    assert report.sentence_count == 8
    assert report.embedded_sentence_count == 8
    assert report.document_source_counts == {
        "confluence": 1,
        "github": 1,
        "jira": 5,
        "slack": 1,
    }
    serialized = json.dumps(report.safe_dict())
    assert "Company body" not in serialized
    assert "permission_set" not in serialized


def test_compatibility_fails_for_a_missing_required_index(db):
    db.execute("DROP INDEX sentences_embedding_idx")

    report = check_compatibility(db)

    assert report.compatible is False
    assert report.issues == ("missing index: sentences_embedding_idx",)


@pytest.mark.parametrize(
    ("drop_sql", "create_sql", "expected_issue"),
    [
        (
            "DROP INDEX chunks_fts_idx",
            "CREATE INDEX chunks_fts_idx ON chunks USING gin (metadata)",
            "invalid index: chunks_fts_idx",
        ),
        (
            "DROP INDEX sentences_embedding_idx",
            "CREATE INDEX sentences_embedding_idx ON sentences "
            "USING hnsw (embedding halfvec_l2_ops)",
            "invalid index: sentences_embedding_idx",
        ),
    ],
)
def test_compatibility_requires_exact_search_index_columns_and_operator_classes(
    db, drop_sql, create_sql, expected_issue
):
    db.execute(drop_sql)
    db.execute(create_sql)

    report = check_compatibility(db)

    assert expected_issue in report.issues


def test_compatibility_rejects_a_partial_search_index(db):
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute(
        "CREATE INDEX chunks_fts_idx ON chunks USING gin (fts) WHERE false"
    )

    report = check_compatibility(db)

    assert "invalid index: chunks_fts_idx" in report.issues


def test_compatibility_fails_for_a_missing_embedding(db):
    db.execute(
        "UPDATE sentences SET embedding = NULL "
        "WHERE source = 'jira' AND chunk_id = 'jira:COMPANY-1:0'"
    )

    report = check_compatibility(db)

    assert report.compatible is False
    assert "sentences contain missing embeddings" in report.issues


def test_compatibility_requires_halfvec_1536_embeddings(db):
    db.execute("DROP INDEX sentences_embedding_idx")
    db.execute("UPDATE sentences SET embedding = NULL")
    db.execute("ALTER TABLE sentences ALTER COLUMN embedding TYPE halfvec(2)")

    report = check_compatibility(db)

    assert "invalid column type: sentences.embedding" in report.issues


def test_compatibility_requires_generated_full_text_values(db):
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute("ALTER TABLE chunks ALTER COLUMN fts DROP EXPRESSION")

    report = check_compatibility(db)

    assert "invalid generated column: chunks.fts" in report.issues


def test_compatibility_requires_the_sentence_identity_shape(db):
    db.execute("ALTER TABLE sentences ALTER COLUMN id DROP IDENTITY")

    report = check_compatibility(db)

    assert "invalid identity column: sentences.id" in report.issues


def test_compatibility_requires_the_exact_full_text_expression(db):
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute("ALTER TABLE chunks DROP COLUMN fts")
    db.execute(
        "ALTER TABLE chunks ADD COLUMN fts tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED"
    )
    db.execute("CREATE INDEX chunks_fts_idx ON chunks USING gin (fts)")

    report = check_compatibility(db)

    assert "invalid generated expression: chunks.fts" in report.issues


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        (
            "ALTER TABLE groups ALTER COLUMN name TYPE varchar(100)",
            "invalid column type: groups.name",
        ),
        (
            "ALTER TABLE groups ALTER COLUMN raw_payload DROP NOT NULL",
            "invalid column nullability: groups.raw_payload",
        ),
    ],
)
def test_compatibility_requires_exact_column_types_and_nullability(
    db, mutation, expected_issue
):
    db.execute(mutation)

    report = check_compatibility(db)

    assert expected_issue in report.issues


def test_compatibility_rejects_a_constraint_name_on_the_wrong_table(db):
    db.execute(
        "ALTER TABLE documents DROP CONSTRAINT documents_root_document_id_fkey"
    )
    db.execute(
        "ALTER TABLE groups ADD CONSTRAINT documents_root_document_id_fkey "
        "UNIQUE (raw_payload)"
    )

    report = check_compatibility(db)

    assert "invalid constraint: documents_root_document_id_fkey" in report.issues


def test_compatibility_rejects_a_foreign_key_to_the_wrong_schema(db):
    db.execute(
        "ALTER TABLE permission_set_users "
        "DROP CONSTRAINT permission_set_users_permission_set_id_fkey"
    )
    db.execute("CREATE SCHEMA impostor")
    db.execute("CREATE TABLE impostor.permission_sets (id uuid PRIMARY KEY)")
    db.execute(
        "INSERT INTO impostor.permission_sets "
        "SELECT DISTINCT permission_set_id FROM permission_set_users"
    )
    db.execute(
        "ALTER TABLE permission_set_users ADD CONSTRAINT "
        "permission_set_users_permission_set_id_fkey "
        "FOREIGN KEY (permission_set_id) REFERENCES impostor.permission_sets(id)"
    )

    report = check_compatibility(db)

    assert (
        "invalid constraint: permission_set_users_permission_set_id_fkey"
        in report.issues
    )


def test_compatibility_rejects_a_search_index_on_the_wrong_relation(db):
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute("CREATE TABLE index_impostor (fts tsvector)")
    db.execute("CREATE INDEX chunks_fts_idx ON index_impostor USING gin (fts)")

    report = check_compatibility(db)

    assert "invalid index: chunks_fts_idx" in report.issues


def test_compatibility_rejects_a_partition_with_the_wrong_source_bound(db):
    db.execute("DELETE FROM sentences WHERE source = 'confluence'")
    db.execute("ALTER TABLE chunks DETACH PARTITION confluence_chunks")
    db.execute("DELETE FROM confluence_chunks")
    db.execute(
        "ALTER TABLE chunks ATTACH PARTITION confluence_chunks FOR VALUES IN ('other')"
    )

    report = check_compatibility(db)

    assert "invalid partition: confluence_chunks" in report.issues


def test_compatibility_rejects_a_partition_on_a_same_named_parent_in_another_schema(db):
    db.execute("DELETE FROM sentences WHERE source = 'confluence'")
    db.execute("ALTER TABLE chunks DETACH PARTITION confluence_chunks")
    db.execute("CREATE SCHEMA impostor")
    db.execute(
        "CREATE TABLE impostor.chunks (LIKE chunks INCLUDING ALL) "
        "PARTITION BY LIST (source)"
    )
    db.execute(
        "ALTER TABLE impostor.chunks ATTACH PARTITION confluence_chunks "
        "FOR VALUES IN ('confluence')"
    )

    report = check_compatibility(db)

    assert "invalid partition: confluence_chunks" in report.issues


def test_compatibility_rejects_a_non_self_rooted_chain(db):
    _seed_malformed_root_chain(db)

    report = check_compatibility(db)

    assert "documents contain non-canonical roots" in report.issues


def test_compatibility_rejects_a_missing_permission_set_relationship(db):
    db.execute("SET LOCAL session_replication_role = replica")
    db.execute(
        "UPDATE documents SET permission_set_id = %s WHERE external_id = 'COMPANY-1'",
        ("20000000-0000-0000-0000-000000000099",),
    )
    db.execute("SET LOCAL session_replication_role = origin")

    report = check_compatibility(db)

    assert "documents contain missing permission sets" in report.issues


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        (
            "permission_set_users",
            "permission_set_id, user_id",
            "'20000000-0000-0000-0000-000000000099', "
            "'00000000-0000-0000-0000-000000000002'",
        ),
        (
            "permission_set_groups",
            "permission_set_id, group_id",
            "'20000000-0000-0000-0000-000000000099', "
            "'10000000-0000-0000-0000-000000000001'",
        ),
        (
            "group_memberships",
            "group_id, user_id",
            "'10000000-0000-0000-0000-000000000099', "
            "'00000000-0000-0000-0000-000000000003'",
        ),
    ],
)
def test_compatibility_rejects_broken_acl_link_relationships(
    db, table, columns, values
):
    db.execute("SET LOCAL session_replication_role = replica")
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({values})")
    db.execute("SET LOCAL session_replication_role = origin")

    report = check_compatibility(db)

    assert f"{table} contain broken relationships" in report.issues


def test_compatibility_reports_a_missing_acl_link_column_without_crashing(db):
    db.execute("ALTER TABLE permission_set_users DROP COLUMN user_id CASCADE")

    report = check_compatibility(db)

    assert "missing column: permission_set_users.user_id" in report.issues


def test_compatibility_reports_an_invalid_content_join_type_without_crashing(db):
    db.execute(
        "ALTER TABLE documents DROP CONSTRAINT documents_permission_set_id_fkey"
    )
    db.execute(
        "ALTER TABLE documents ALTER COLUMN permission_set_id TYPE text "
        "USING permission_set_id::text"
    )

    report = check_compatibility(db)

    assert "invalid column type: documents.permission_set_id" in report.issues


def test_cli_output_is_aggregate_only_and_never_echoes_credentials(prepared_test_database):
    secret = "do-not-print-this-password"
    env = {
        **os.environ,
        "DATABASE_URL": _test_database_url().replace(
            "postgres:postgres", f"postgres:{secret}"
        ),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }

    completed = subprocess.run(
        [sys.executable, "-m", "knowledge_browser.db_compat"],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "compatibility check failed: database connection unavailable\n"
    assert secret not in completed.stdout + completed.stderr


def test_test_database_guard_rejects_any_database_without_test(monkeypatch):
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/knowledge_search",
    )

    with pytest.raises(RuntimeError, match="dedicated _test database"):
        _test_database_url()
