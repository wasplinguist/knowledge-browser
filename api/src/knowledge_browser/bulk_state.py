"""Safe database reset and resumable bulk-import state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .db_compat import REQUIRED_TABLES as PRODUCT_TABLES


SOURCES = ("jira", "confluence", "slack", "github")
STATE_TABLES = {"bulk_import_runs", "bulk_import_progress", "bulk_embedding_cache"}
REQUIRED_TABLES = {
    *PRODUCT_TABLES,
    "search_events",
    "search_clicks",
    *STATE_TABLES,
}
POPULATED_SQL = """
    SELECT
      EXISTS (SELECT FROM public.users)
      OR EXISTS (SELECT FROM public.groups)
      OR EXISTS (SELECT FROM public.group_memberships)
      OR EXISTS (SELECT FROM public.permission_sets)
      OR EXISTS (SELECT FROM public.permission_set_users)
      OR EXISTS (SELECT FROM public.permission_set_groups)
      OR EXISTS (SELECT FROM public.documents)
      OR EXISTS (SELECT FROM public.chunks)
      OR EXISTS (SELECT FROM public.sentences)
"""


class BulkStateError(Exception):
    """A safe, actionable bulk-import state error."""

    safe_code = "schema_failure"

    def __init__(self, message: str, *, safe_code: str | None = None):
        super().__init__(message)
        if safe_code is not None:
            self.safe_code = safe_code


@dataclass(frozen=True, slots=True)
class BulkRun:
    id: UUID
    manifest_digest: str
    dataset_version: str
    embedding_model: str
    embedding_dimensions: int
    status: str
    safe_error: str | None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Progress:
    run_id: UUID
    source: str
    next_line: int
    next_offset: int
    documents: int
    chunks: int
    sentences: int


def assert_redwood_database(database_url: str) -> None:
    """Refuse destructive work unless psycopg parses the exact Redwood name."""
    try:
        database = conninfo_to_dict(database_url).get("dbname")
    except psycopg.ProgrammingError as error:
        raise BulkStateError("database name must be exactly knowledge_redwood") from error
    if database != "knowledge_redwood":
        raise BulkStateError("database name must be exactly knowledge_redwood")


def assert_import_database(database_url: str) -> None:
    """Allow imports only into the product or isolated Redwood database."""
    try:
        database = conninfo_to_dict(database_url).get("dbname")
    except psycopg.ProgrammingError as error:
        raise BulkStateError("database must be an approved product database") from error
    if database not in {"knowledge_search", "knowledge_redwood"}:
        raise BulkStateError("database must be an approved product database")


def reset_redwood_database(
    database_url: str, schema_paths: Sequence[Path]
) -> None:
    """Recreate only the explicitly guarded Redwood database schema."""
    assert_redwood_database(database_url)
    with psycopg.connect(database_url) as conn:
        conn.execute("SET LOCAL search_path TO public, pg_catalog")
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        for schema_path in schema_paths:
            conn.execute(schema_path.read_text())
        conn.execute("DROP INDEX IF EXISTS public.chunks_fts_idx")
        conn.execute("DROP INDEX IF EXISTS public.sentences_embedding_idx")


def _required_tables(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public' AND tablename = ANY(%s)
            """,
            (list(REQUIRED_TABLES),),
        )
    }


def _load_run(row) -> BulkRun:
    return BulkRun(*row)


def start_or_resume_run(conn, validated, model, dimensions) -> BulkRun:
    """Start on an empty database or resume only exactly matching state."""
    with conn.transaction():
        conn.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(20260902, 2)"
        )
        return _start_or_resume_run(conn, validated, model, dimensions)


def _start_or_resume_run(conn, validated, model, dimensions) -> BulkRun:
    if _required_tables(conn) != REQUIRED_TABLES:
        raise BulkStateError("database is partially initialized")

    rows = conn.execute(
        """
        SELECT id, manifest_digest, dataset_version, embedding_model,
               embedding_dimensions, status, safe_error, started_at,
               updated_at, completed_at
        FROM public.bulk_import_runs
        ORDER BY started_at
        """
    ).fetchall()
    dataset_version = validated.manifest["dataset_version"]
    if not rows:
        if conn.execute(POPULATED_SQL).fetchone()[0]:
            raise BulkStateError("populated database without matching state")
        run_id = uuid4()
        row = conn.execute(
            """
            INSERT INTO public.bulk_import_runs (
              id, manifest_digest, dataset_version, embedding_model,
              embedding_dimensions, status
            ) VALUES (%s, %s, %s, %s, %s, 'loading')
            RETURNING id, manifest_digest, dataset_version, embedding_model,
                      embedding_dimensions, status, safe_error, started_at,
                      updated_at, completed_at
            """,
            (
                run_id,
                validated.manifest_digest,
                dataset_version,
                model,
                dimensions,
            ),
        ).fetchone()
        for source in SOURCES:
            conn.execute(
                "INSERT INTO public.bulk_import_progress (run_id, source) VALUES (%s, %s)",
                (run_id, source),
            )
        return _load_run(row)

    if len(rows) != 1:
        raise BulkStateError("database contains incompatible bulk import state")
    run = _load_run(rows[0])
    if run.manifest_digest != validated.manifest_digest:
        raise BulkStateError(
            "bulk import manifest changed", safe_code="changed_state"
        )
    if run.dataset_version != dataset_version:
        raise BulkStateError(
            "bulk import dataset version changed", safe_code="changed_state"
        )
    if run.embedding_model != model:
        raise BulkStateError(
            "bulk import model changed", safe_code="changed_state"
        )
    if run.embedding_dimensions != dimensions:
        raise BulkStateError(
            "bulk import dimensions changed", safe_code="changed_state"
        )

    progress_sources = {
        row[0]
        for row in conn.execute(
            "SELECT source FROM public.bulk_import_progress WHERE run_id = %s",
            (run.id,),
        )
    }
    if progress_sources != set(SOURCES):
        raise BulkStateError("database is partially initialized")
    return run


def load_progress(conn, run_id, source) -> Progress:
    """Load one source checkpoint for a bulk run."""
    row = conn.execute(
        """
        SELECT run_id, source, next_line, next_offset, documents, chunks, sentences
        FROM public.bulk_import_progress
        WHERE run_id = %s AND source = %s
        """,
        (run_id, source),
    ).fetchone()
    if row is None:
        raise BulkStateError("bulk import progress is missing")
    return Progress(*row)


def save_progress(
    conn,
    run_id,
    source,
    *,
    next_line,
    next_offset,
    documents=None,
    chunks=None,
    sentences=None,
) -> None:
    """Advance a checkpoint in the caller's data transaction."""
    result = conn.execute(
        """
        UPDATE public.bulk_import_progress
        SET next_line = %s,
            next_offset = %s,
            documents = COALESCE(%s, documents),
            chunks = COALESCE(%s, chunks),
            sentences = COALESCE(%s, sentences)
        WHERE run_id = %s AND source = %s
        """,
        (next_line, next_offset, documents, chunks, sentences, run_id, source),
    )
    if result.rowcount != 1:
        raise BulkStateError("bulk import progress is missing")
    conn.execute(
        "UPDATE public.bulk_import_runs SET updated_at = pg_catalog.now() WHERE id = %s",
        (run_id,),
    )


def touch_run(conn, run_id) -> None:
    """Refresh the safe operator heartbeat inside a short transaction."""
    result = conn.execute(
        "UPDATE public.bulk_import_runs SET updated_at = pg_catalog.now() WHERE id = %s",
        (run_id,),
    )
    if result.rowcount != 1:
        raise BulkStateError("bulk import run is missing")


def configure_run(
    conn, run_id, *, request_concurrency, stall_after_seconds
) -> None:
    result = conn.execute(
        """
        UPDATE public.bulk_import_runs
        SET request_concurrency = %s, stall_after_seconds = %s,
            updated_at = pg_catalog.now()
        WHERE id = %s
        """,
        (request_concurrency, stall_after_seconds, run_id),
    )
    if result.rowcount != 1:
        raise BulkStateError("bulk import run is missing")


def record_run_metrics(
    conn,
    run_id,
    *,
    cache_hits,
    cache_misses,
    provider_requests,
    request_concurrency,
    retries,
    sentences_per_second,
    estimated_remaining_seconds,
) -> None:
    result = conn.execute(
        """
        UPDATE public.bulk_import_runs
        SET cache_hits = cache_hits + %s,
            cache_misses = cache_misses + %s,
            provider_requests = provider_requests + %s,
            request_concurrency = GREATEST(request_concurrency, %s),
            retries = retries + %s,
            sentences_per_second = %s,
            estimated_remaining_seconds = %s,
            updated_at = pg_catalog.now()
        WHERE id = %s
        """,
        (
            cache_hits,
            cache_misses,
            provider_requests,
            request_concurrency,
            retries,
            sentences_per_second,
            estimated_remaining_seconds,
            run_id,
        ),
    )
    if result.rowcount != 1:
        raise BulkStateError("bulk import run is missing")
