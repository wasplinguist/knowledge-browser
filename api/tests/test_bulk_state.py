from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import psycopg
import pytest

import knowledge_browser.bulk_state as bulk_state
from conftest import _prepare_test_database
from knowledge_browser.bulk_state import (
    BulkRun,
    BulkStateError,
    Progress,
    assert_redwood_database,
    load_progress,
    reset_redwood_database,
    save_progress,
    start_or_resume_run,
    touch_run,
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
        conn.execute("SELECT 1")
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


def test_reset_guard_accepts_exact_redwood_database():
    assert_redwood_database(
        "postgresql://postgres:postgres@localhost/knowledge_redwood"
    )


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


def test_reset_pins_public_schema_with_hostile_search_path(
    prepared_test_database, monkeypatch
):
    hostile_url = (
        f"{prepared_test_database}?options=-csearch_path%3Dhostile"
    )
    with psycopg.connect(prepared_test_database) as conn:
        conn.execute("CREATE SCHEMA hostile")

    monkeypatch.setattr(bulk_state, "assert_redwood_database", lambda _url: None)
    try:
        reset_redwood_database(hostile_url, SCHEMAS)

        with psycopg.connect(prepared_test_database) as conn:
            assert conn.execute("SELECT to_regclass('public.users')").fetchone() == (
                "users",
            )
            assert conn.execute("SELECT to_regclass('hostile.users')").fetchone() == (
                None,
            )
    finally:
        with psycopg.connect(prepared_test_database) as conn:
            conn.execute("DROP SCHEMA IF EXISTS hostile CASCADE")
        _prepare_test_database()


def test_reset_rolls_back_if_a_schema_file_fails(
    prepared_test_database, monkeypatch, tmp_path
):
    invalid_schema = tmp_path / "invalid.sql"
    invalid_schema.write_text("CREATE TABLE public.broken (")
    with psycopg.connect(prepared_test_database) as conn:
        conn.execute(
            "INSERT INTO users (email, name) VALUES ('preserved@test', 'Preserved')"
        )

    monkeypatch.setattr(bulk_state, "assert_redwood_database", lambda _url: None)
    try:
        with pytest.raises(psycopg.Error):
            reset_redwood_database(
                prepared_test_database, (SCHEMAS[0], invalid_schema)
            )

        with psycopg.connect(prepared_test_database) as conn:
            assert conn.execute(
                "SELECT name FROM users WHERE email = 'preserved@test'"
            ).fetchone() == ("Preserved",)
    finally:
        with psycopg.connect(prepared_test_database) as conn:
            conn.execute("DELETE FROM users WHERE email = 'preserved@test'")


def test_reset_rolls_back_schema_and_data_if_deferred_index_drop_fails(
    prepared_test_database, monkeypatch
):
    with psycopg.connect(prepared_test_database) as conn:
        conn.execute(
            "INSERT INTO users (email, name) VALUES ('preserved@test', 'Preserved')"
        )
        conn.execute("CREATE SCHEMA test_reset_guard")
        conn.execute(
            """
            CREATE FUNCTION test_reset_guard.fail_deferred_index_drop()
            RETURNS event_trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              RAISE EXCEPTION 'forced deferred index failure';
            END
            $$
            """
        )
        conn.execute(
            """
            CREATE EVENT TRIGGER fail_deferred_index_drop
            ON ddl_command_start
            WHEN TAG IN ('DROP INDEX')
            EXECUTE FUNCTION test_reset_guard.fail_deferred_index_drop()
            """
        )

    monkeypatch.setattr(bulk_state, "assert_redwood_database", lambda _url: None)
    try:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="forced deferred index failure",
        ):
            reset_redwood_database(prepared_test_database, SCHEMAS)

        with psycopg.connect(prepared_test_database) as conn:
            assert conn.execute(
                "SELECT name FROM users WHERE email = 'preserved@test'"
            ).fetchone() == ("Preserved",)
            assert conn.execute(
                """
                SELECT count(*)
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN ('chunks_fts_idx', 'sentences_embedding_idx')
                """
            ).fetchone() == (2,)
    finally:
        with psycopg.connect(prepared_test_database) as conn:
            conn.execute("DROP EVENT TRIGGER IF EXISTS fail_deferred_index_drop")
            conn.execute("DROP SCHEMA IF EXISTS test_reset_guard CASCADE")
        _prepare_test_database()


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


def test_autocommit_initialization_rolls_back_partial_checkpoints(
    prepared_test_database, validated
):
    with psycopg.connect(prepared_test_database, autocommit=True) as conn:
        conn.execute("TRUNCATE public.bulk_import_runs CASCADE")
        conn.execute(
            """
            CREATE FUNCTION public.fail_slack_progress() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.source = 'slack' THEN
                RAISE EXCEPTION 'forced checkpoint failure';
              END IF;
              RETURN NEW;
            END
            $$
            """
        )
        conn.execute(
            """
            CREATE TRIGGER fail_slack_progress
            BEFORE INSERT ON public.bulk_import_progress
            FOR EACH ROW EXECUTE FUNCTION public.fail_slack_progress()
            """
        )
        try:
            with pytest.raises(
                psycopg.errors.RaiseException, match="forced checkpoint failure"
            ):
                start_or_resume_run(
                    conn, validated, "text-embedding-3-small", 1536
                )

            assert conn.execute(
                "SELECT count(*) FROM public.bulk_import_runs"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM public.bulk_import_progress"
            ).fetchone() == (0,)
        finally:
            conn.execute(
                "DROP TRIGGER IF EXISTS fail_slack_progress ON public.bulk_import_progress"
            )
            conn.execute("DROP FUNCTION IF EXISTS public.fail_slack_progress()")
            conn.execute("TRUNCATE public.bulk_import_runs CASCADE")


def test_concurrent_initialization_creates_only_one_run(prepared_test_database):
    barrier = Barrier(2)

    def initialize(digest):
        with psycopg.connect(prepared_test_database, autocommit=True) as conn:
            barrier.wait()
            try:
                return start_or_resume_run(
                    conn,
                    SimpleNamespace(
                        manifest_digest=digest,
                        manifest={"dataset_version": "0.1.0"},
                    ),
                    "text-embedding-3-small",
                    1536,
                )
            except BulkStateError as error:
                return error

    with psycopg.connect(prepared_test_database, autocommit=True) as conn:
        conn.execute("TRUNCATE public.bulk_import_runs CASCADE")
        conn.execute(
            """
            CREATE FUNCTION public.delay_bulk_run() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              PERFORM pg_sleep(0.5);
              RETURN NEW;
            END
            $$
            """
        )
        conn.execute(
            """
            CREATE TRIGGER delay_bulk_run
            BEFORE INSERT ON public.bulk_import_runs
            FOR EACH ROW EXECUTE FUNCTION public.delay_bulk_run()
            """
        )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(initialize, ("first-manifest", "second-manifest"))
            )

        assert sum(isinstance(result, BulkRun) for result in results) == 1
        assert sum(isinstance(result, BulkStateError) for result in results) == 1
        with psycopg.connect(prepared_test_database) as conn:
            assert conn.execute(
                "SELECT count(*) FROM public.bulk_import_runs"
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT count(*) FROM public.bulk_import_progress"
            ).fetchone() == (4,)
    finally:
        with psycopg.connect(prepared_test_database, autocommit=True) as conn:
            conn.execute(
                "DROP TRIGGER IF EXISTS delay_bulk_run ON public.bulk_import_runs"
            )
            conn.execute("DROP FUNCTION IF EXISTS public.delay_bulk_run()")
            conn.execute("TRUNCATE public.bulk_import_runs CASCADE")


def test_state_operations_ignore_hostile_search_path(db, validated):
    db.execute("CREATE SCHEMA IF NOT EXISTS hostile")
    db.execute("SET LOCAL search_path TO hostile")

    run = start_or_resume_run(db, validated, "text-embedding-3-small", 1536)
    save_progress(db, run.id, "jira", next_line=2, next_offset=100)

    assert load_progress(db, run.id, "jira").next_line == 2


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

    with pytest.raises(BulkStateError, match=message) as captured:
        start_or_resume_run(db, changed, model, dimensions)
    assert captured.value.safe_code == "changed_state"


def test_start_rejects_partially_initialized_state_schema(db, validated):
    db.execute("DROP TABLE public.bulk_embedding_cache")

    with pytest.raises(BulkStateError, match="partially initialized"):
        start_or_resume_run(db, validated, "text-embedding-3-small", 1536)


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE public.documents CASCADE",
        "DROP TABLE public.sentences CASCADE",
        "DROP TABLE public.jira_chunks CASCADE",
        "DROP TABLE public.search_clicks",
    ],
)
def test_start_rejects_partially_initialized_product_schema(
    db, validated, statement
):
    db.execute(statement)

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


def test_touch_run_refreshes_the_stall_heartbeat(db, run):
    db.execute(
        "UPDATE bulk_import_runs SET updated_at = '2000-01-01' WHERE id = %s",
        (run.id,),
    )

    touch_run(db, run.id)

    assert db.execute(
        "SELECT updated_at > '2000-01-01' FROM bulk_import_runs WHERE id = %s",
        (run.id,),
    ).fetchone() == (True,)


def test_progress_and_rows_rollback_together(db, run):
    with pytest.raises(RuntimeError):
        with db.transaction():
            save_progress(db, run.id, "slack", next_line=2, next_offset=100)
            db.execute("INSERT INTO users (email,name) VALUES ('rollback@test','Rollback')")
            raise RuntimeError("stop")
    assert load_progress(db, run.id, "slack").next_line == 1
    assert db.execute("SELECT count(*) FROM users WHERE email='rollback@test'").fetchone() == (0,)
