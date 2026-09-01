from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

import knowledge_browser.bulk_state as bulk_state
from knowledge_browser.bulk_state import (
    BulkRun,
    BulkStateError,
    Progress,
    assert_redwood_database,
    load_progress,
    reset_redwood_database,
    save_progress,
    start_or_resume_run,
)


SCHEMAS = (
    Path(__file__).parents[2] / "db" / "init" / "001_schema.sql",
    Path(__file__).parents[2] / "db" / "init" / "002_bulk_import.sql",
)
SOURCES = {"jira", "confluence", "slack", "github"}

pytestmark = pytest.mark.integration


@pytest.fixture
def db(prepared_test_database):
    with psycopg.connect(prepared_test_database) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def validated():
    return SimpleNamespace(
        manifest_digest="manifest-digest",
        manifest={"dataset_version": "0.1.0"},
    )


@pytest.fixture
def run(db, validated):
    return start_or_resume_run(db, validated, "text-embedding-3-small", 1536)


@pytest.mark.parametrize("name", ["knowledge_search", "postgres", "knowledge_redwood_test"])
def test_reset_guard_refuses_every_other_database(name):
    with pytest.raises(BulkStateError, match="exactly knowledge_redwood"):
        assert_redwood_database(f"postgresql://postgres:postgres@localhost/{name}")


def test_reset_rebuilds_only_the_dedicated_test_database(
    prepared_test_database, monkeypatch
):
    with psycopg.connect(prepared_test_database) as conn:
        conn.execute(
            "INSERT INTO users (email, name) VALUES ('reset@test', 'Reset')"
        )

    monkeypatch.setattr(bulk_state, "assert_redwood_database", lambda _url: None)
    reset_redwood_database(prepared_test_database, SCHEMAS)

    with psycopg.connect(prepared_test_database) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)
        assert {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        } >= {"users", "bulk_import_runs", "bulk_import_progress", "bulk_embedding_cache"}


def test_start_creates_run_and_all_source_progress(db, validated):
    run = start_or_resume_run(db, validated, "text-embedding-3-small", 1536)

    assert isinstance(run, BulkRun)
    assert run.manifest_digest == "manifest-digest"
    assert run.dataset_version == "0.1.0"
    assert run.embedding_model == "text-embedding-3-small"
    assert run.embedding_dimensions == 1536
    assert run.status == "loading"
    progress = {
        source: load_progress(db, run.id, source)
        for source in SOURCES
    }
    assert all(isinstance(item, Progress) for item in progress.values())
    assert {item.source for item in progress.values()} == SOURCES
    assert all(
        (item.next_line, item.next_offset, item.documents, item.chunks, item.sentences)
        == (1, 0, 0, 0, 0)
        for item in progress.values()
    )


def test_matching_run_resumes_with_the_same_id(db, validated):
    first = start_or_resume_run(db, validated, "text-embedding-3-small", 1536)

    resumed = start_or_resume_run(db, validated, "text-embedding-3-small", 1536)

    assert resumed.id == first.id


@pytest.mark.parametrize(
    ("digest", "model", "dimensions", "message"),
    [
        ("changed", "text-embedding-3-small", 1536, "manifest"),
        ("manifest-digest", "changed-model", 1536, "model"),
        ("manifest-digest", "text-embedding-3-small", 3072, "dimensions"),
    ],
)
def test_resume_rejects_changed_configuration(
    db, validated, digest, model, dimensions, message
):
    start_or_resume_run(db, validated, "text-embedding-3-small", 1536)
    changed = SimpleNamespace(
        manifest_digest=digest,
        manifest=validated.manifest,
    )

    with pytest.raises(BulkStateError, match=message):
        start_or_resume_run(db, changed, model, dimensions)


def test_start_rejects_partially_initialized_state_schema(db, validated):
    db.execute("DROP TABLE bulk_embedding_cache")

    with pytest.raises(BulkStateError, match="partially initialized"):
        start_or_resume_run(db, validated, "text-embedding-3-small", 1536)


def test_start_rejects_populated_database_without_matching_state(db, validated):
    db.execute("INSERT INTO users (email, name) VALUES ('existing@test', 'Existing')")

    with pytest.raises(BulkStateError, match="populated database without matching state"):
        start_or_resume_run(db, validated, "text-embedding-3-small", 1536)


def test_save_and_load_progress(db, run):
    save_progress(
        db,
        run.id,
        "jira",
        next_line=8,
        next_offset=987,
        documents=7,
        chunks=14,
        sentences=21,
    )

    assert load_progress(db, run.id, "jira") == Progress(
        run_id=run.id,
        source="jira",
        next_line=8,
        next_offset=987,
        documents=7,
        chunks=14,
        sentences=21,
    )


def test_progress_and_rows_rollback_together(db, run):
    with pytest.raises(RuntimeError):
        with db.transaction():
            save_progress(db, run.id, "slack", next_line=2, next_offset=100)
            db.execute("INSERT INTO users (email,name) VALUES ('rollback@test','Rollback')")
            raise RuntimeError("stop")
    assert load_progress(db, run.id, "slack").next_line == 1
    assert db.execute("SELECT count(*) FROM users WHERE email='rollback@test'").fetchone() == (0,)
