"""Resumable document and embedding batch coordination."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import batched, islice
from time import monotonic
from uuid import UUID

from .bulk_state import load_progress, save_progress, start_or_resume_run
from .bulk_writer import (
    BatchReport,
    import_identities,
    write_document_batch,
)
from .dataset import SOURCES, iter_artifacts
from .db_compat import check_compatibility
from .embedding_index import (
    EmbeddingRequestConfig,
    EmbeddingRequestResult,
    collect_sentences,
    load_cached_embeddings,
    persist_embeddings,
    request_missing_embeddings,
)


MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
IMPORT_LOCK = (20260902, 4)
SAFE_ERRORS = {
    "batch_import_failed",
    "embedding_provider_failed",
    "embedding_provider_invalid_response",
    "missing_api_key",
}


@dataclass(frozen=True, slots=True)
class ImportResult:
    run_id: UUID
    complete: bool
    batches: tuple[BatchReport, ...]

    @property
    def provider_calls(self):
        return sum(batch.provider_calls for batch in self.batches)


def _set_run_state(conn, run_id, status, safe_error=None):
    result = conn.execute(
        """
        UPDATE public.bulk_import_runs
        SET status = %s,
            safe_error = %s,
            updated_at = pg_catalog.now(),
            completed_at = CASE
              WHEN %s = 'complete' THEN pg_catalog.now()
              ELSE NULL
            END
        WHERE id = %s
        """,
        (status, safe_error, status, run_id),
    )
    if result.rowcount != 1:
        raise RuntimeError("bulk import run is missing")


def finalize_indexes(conn, run_id) -> None:
    """Build missing search indexes and complete the import atomically."""
    _set_run_state(conn, run_id, "indexing")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS chunks_fts_idx "
        "ON public.chunks USING gin (fts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS sentences_embedding_idx "
        "ON public.sentences USING hnsw (embedding halfvec_cosine_ops)"
    )
    conn.execute(
        "ANALYZE public.users, public.documents, public.chunks, public.sentences"
    )
    report = check_compatibility(conn)
    if any("index:" in issue for issue in report.issues):
        raise RuntimeError("bulk import indexes failed compatibility checks")
    _set_run_state(conn, run_id, "complete")


def prepare_bulk_load(conn) -> None:
    """Remove only indexes intentionally deferred during the Redwood load."""
    conn.execute("DROP INDEX IF EXISTS public.chunks_fts_idx")
    conn.execute("DROP INDEX IF EXISTS public.sentences_embedding_idx")


def _record_failure(connection_factory, run_id, error):
    safe_error = getattr(error, "safe_code", "batch_import_failed")
    if safe_error not in SAFE_ERRORS:
        safe_error = "batch_import_failed"
    try:
        with connection_factory() as conn:
            with conn.transaction():
                _set_run_state(conn, run_id, "failed", safe_error)
    except Exception:
        pass


def _acquire_import_lock(conn):
    conn.execute(
        "SELECT pg_catalog.pg_advisory_lock(%s, %s)", IMPORT_LOCK
    )
    conn.commit()


def _release_import_lock(conn):
    try:
        conn.execute(
            "SELECT pg_catalog.pg_advisory_unlock(%s, %s)", IMPORT_LOCK
        )
        conn.commit()
    except Exception:
        pass


def run_import(
    connection_factory,
    dataset,
    client_factory,
    document_batch_size=100,
    embedding_batch_size=100,
    work_window_size=200,
    request_config=None,
    stop_after_batches=None,
    progress_callback=None,
):
    """Start or resume a validated dataset import in committed batches."""
    if document_batch_size <= 0:
        raise ValueError("document_batch_size must be positive")
    if embedding_batch_size <= 0:
        raise ValueError("embedding_batch_size must be positive")
    if work_window_size <= 0:
        raise ValueError("work_window_size must be positive")
    if stop_after_batches is not None and stop_after_batches < 0:
        raise ValueError("stop_after_batches must not be negative")
    started = monotonic()
    request_config = request_config or EmbeddingRequestConfig(
        max_inputs=embedding_batch_size
    )

    run = None
    failure_recorded = False
    try:
        with connection_factory() as conn:
            _acquire_import_lock(conn)
            try:
                run = start_or_resume_run(conn, dataset, MODEL, DIMENSIONS)
                if run.status == "complete":
                    return ImportResult(run.id, True, ())
                with conn.transaction():
                    _set_run_state(conn, run.id, "loading")
                    identities = import_identities(conn, dataset.context)

                reports = []
                client = None
                if stop_after_batches == 0:
                    return ImportResult(run.id, False, ())

                for source in SOURCES:
                    with conn.transaction():
                        progress = load_progress(conn, run.id, source)
                    records = iter_artifacts(
                        dataset,
                        source,
                        start_offset=progress.next_offset,
                        start_line=progress.next_line,
                    )
                    while window := tuple(islice(records, work_window_size)):
                        window_sentences = collect_sentences(
                            [record.document for record in window]
                        )
                        with conn.transaction():
                            cached = load_cached_embeddings(
                                conn, run, window_sentences
                            )
                        missing = [
                            sentence
                            for sentence in window_sentences
                            if sentence not in cached
                        ]
                        requested = EmbeddingRequestResult(
                            {}, 0, 0, request_config.concurrency
                        )
                        if missing:
                            if client is None:
                                client = client_factory()
                            requested = request_missing_embeddings(
                                client,
                                run.embedding_model,
                                missing,
                                request_config,
                            )
                            with conn.transaction():
                                persist_embeddings(conn, run, requested.vectors)
                        embeddings = {**cached, **requested.vectors}

                        first_batch = True
                        for batch in batched(window, document_batch_size):
                            with conn.transaction():
                                report = write_document_batch(
                                    conn, run, batch, identities, embeddings
                                )
                                save_progress(
                                    conn,
                                    run.id,
                                    source,
                                    next_line=report.next_line,
                                    next_offset=report.next_offset,
                                    documents=(
                                        progress.documents + report.documents
                                    ),
                                    chunks=progress.chunks + report.chunks,
                                    sentences=(
                                        progress.sentences + report.sentences
                                    ),
                                )
                            report = replace(
                                report,
                                elapsed_seconds=monotonic() - started,
                                provider_calls=(
                                    requested.provider_requests
                                    if first_batch
                                    else 0
                                ),
                                cache_hits=len(cached) if first_batch else 0,
                                cache_misses=len(missing) if first_batch else 0,
                                concurrency=(
                                    requested.concurrency if first_batch else 0
                                ),
                                retries=requested.retries if first_batch else 0,
                            )
                            first_batch = False
                            if progress_callback is not None:
                                progress_callback(report)
                            reports.append(report)
                            progress = replace(
                                progress,
                                next_line=report.next_line,
                                next_offset=report.next_offset,
                                documents=(
                                    progress.documents + report.documents
                                ),
                                chunks=progress.chunks + report.chunks,
                                sentences=(
                                    progress.sentences + report.sentences
                                ),
                            )
                            if (
                                stop_after_batches is not None
                                and len(reports) >= stop_after_batches
                            ):
                                return ImportResult(
                                    run.id, False, tuple(reports)
                                )
                    source_size = (
                        dataset.root / "artifacts" / f"{source}.jsonl"
                    ).stat().st_size
                    if (
                        progress.next_offset != source_size
                        or progress.next_line != progress.documents + 1
                    ):
                        raise RuntimeError("source checkpoint is incomplete")

                loaded_documents = conn.execute(
                    """
                    SELECT COALESCE(sum(documents), 0)
                    FROM public.bulk_import_progress
                    WHERE run_id = %s
                    """,
                    (run.id,),
                ).fetchone()[0]
                expected_documents = dataset.manifest.get("counts", {}).get(
                    "artifacts"
                )
                if (
                    expected_documents is not None
                    and loaded_documents != expected_documents
                ):
                    raise RuntimeError("source checkpoint is incomplete")

                with conn.transaction():
                    _set_run_state(conn, run.id, "indexing")
                with conn.transaction():
                    finalize_indexes(conn, run.id)
                return ImportResult(run.id, True, tuple(reports))
            except Exception as error:
                if run is not None:
                    failure_recorded = True
                    _record_failure(connection_factory, run.id, error)
                raise
            finally:
                _release_import_lock(conn)
    except Exception as error:
        if run is not None and not failure_recorded:
            _record_failure(connection_factory, run.id, error)
        raise
