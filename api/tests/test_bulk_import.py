from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from openai import APIStatusError, OpenAI

from conftest import _prepare_test_database
import knowledge_browser.bulk_import as bulk_import
from knowledge_browser.bulk_import import run_import
from knowledge_browser.bulk_state import load_progress, start_or_resume_run
from knowledge_browser.bulk_writer import import_identities, write_document_batch
from knowledge_browser.dataset import (
    SOURCES,
    ValidatedDataset,
    iter_artifacts,
    validate_streaming_dataset,
)
from knowledge_browser.embedding_index import (
    collect_sentences,
    embed_missing,
    load_cached_embeddings,
    persist_embeddings,
    sentence_key,
)


DATA = Path(__file__).parents[2] / "data" / "redwood"
MODEL = "text-embedding-3-small"
VECTOR = [0.0] * 1536
pytestmark = pytest.mark.integration


class FakeEmbeddingClient:
    requests = []

    def __init__(self):
        self.calls = 0
        self.embeddings = self

    def create(self, *, model, input):
        self.calls += 1
        type(self).requests.append((model, tuple(input)))
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=VECTOR)
                for index, _ in enumerate(input)
            ]
        )


@pytest.fixture(scope="module")
def validated_company():
    return validate_streaming_dataset(DATA)


@pytest.fixture
def tiny_dataset(tmp_path, validated_company):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with (DATA / "artifacts" / "jira.jsonl").open("rb") as source:
        first_three = b"".join(source.readline() for _ in range(3))
    for source in SOURCES:
        (artifacts / f"{source}.jsonl").write_bytes(
            first_three if source == "jira" else b""
        )
    return ValidatedDataset(
        root=tmp_path,
        manifest={"dataset_version": "tiny-v1", "counts": {"artifacts": 3}},
        manifest_digest="tiny-manifest",
        context={
            **validated_company.context,
            "users": validated_company.context["users"][:1],
            "identity_groups": (),
        },
    )


@pytest.fixture
def connection_factory():
    database_url = _prepare_test_database()
    return lambda: psycopg.connect(database_url)


@pytest.fixture
def db(connection_factory):
    with connection_factory() as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def run(db, tiny_dataset):
    return start_or_resume_run(db, tiny_dataset, MODEL, 1536)


@pytest.fixture(autouse=True)
def reset_fake_requests():
    FakeEmbeddingClient.requests = []


def test_embedding_cache_avoids_repeat_calls(db, run):
    client = FakeEmbeddingClient()

    first = embed_missing(db, run.id, client, MODEL, ["same sentence"])
    second = embed_missing(db, run.id, client, MODEL, ["same sentence"])

    assert first == second
    assert client.calls == 1
    assert db.execute(
        "SELECT sentence FROM bulk_embedding_cache WHERE run_id = %s",
        (run.id,),
    ).fetchall() == [("same sentence",)]


def test_embedding_cache_rejects_a_hash_for_different_text(db, run):
    db.execute(
        """
        INSERT INTO bulk_embedding_cache (run_id, content_hash, sentence, embedding)
        VALUES (%s, %s, %s, %s::halfvec)
        """,
        (run.id, sentence_key("expected"), "different", "[" + ",".join(["0"] * 1536) + "]"),
    )

    with pytest.raises(ValueError, match="embedding cache hash collision"):
        embed_missing(db, run.id, FakeEmbeddingClient(), MODEL, ["expected"])


def test_embedding_cache_rejects_invalid_provider_indexes(db, run):
    class InvalidClient:
        embeddings = None

        def __init__(self):
            self.embeddings = self

        def create(self, **_request):
            return SimpleNamespace(
                data=[SimpleNamespace(index=1, embedding=VECTOR)]
            )

    with pytest.raises(ValueError, match="embedding provider returned invalid indexes"):
        embed_missing(db, run.id, InvalidClient(), MODEL, ["new sentence"])

    assert db.execute(
        "SELECT count(*) FROM bulk_embedding_cache WHERE run_id = %s", (run.id,)
    ).fetchone() == (0,)


def test_bulk_cache_read_and_write_preserve_sentence_vectors(db, run):
    vector = "[" + ",".join(["0"] * 1536) + "]"

    persist_embeddings(db, run, {"one sentence": vector, "second sentence": vector})

    assert load_cached_embeddings(
        db, run, ["second sentence", "missing", "one sentence"]
    ) == {
        "second sentence": vector,
        "one sentence": vector,
    }


def test_bulk_cache_write_uses_copy_not_executemany(db, run):
    used_copy = False

    class CursorSpy:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def copy(self, statement):
            nonlocal used_copy
            used_copy = True
            return self.cursor.copy(statement)

        def executemany(self, *_args, **_kwargs):
            raise AssertionError("cache persistence must use COPY")

    class ConnectionSpy:
        def execute(self, *args, **kwargs):
            return db.execute(*args, **kwargs)

        def cursor(self):
            return CursorSpy(db.cursor())

    vector = "[" + ",".join(["1"] * 1536) + "]"
    persist_embeddings(ConnectionSpy(), run, {"copied sentence": vector})

    assert used_copy is True
    assert load_cached_embeddings(db, run, ["copied sentence"]) == {
        "copied sentence": vector
    }


def test_bulk_cache_rejects_run_model_or_dimension_mismatch(db, run):
    vector = "[" + ",".join(["0"] * 1536) + "]"

    with pytest.raises(ValueError, match="embedding cache run mismatch"):
        load_cached_embeddings(db, replace(run, embedding_model="other"), ["one"])
    with pytest.raises(ValueError, match="embedding cache run mismatch"):
        persist_embeddings(
            db,
            replace(run, embedding_dimensions=2),
            {"one": vector},
        )


def test_bulk_cache_rejects_hash_collision_set_wide(db, run, monkeypatch):
    vector = "[" + ",".join(["0"] * 1536) + "]"
    db.execute(
        """
        INSERT INTO bulk_embedding_cache (run_id, content_hash, sentence, embedding)
        VALUES (%s, %s, %s, %s::halfvec)
        """,
        (run.id, "forced-hash", "existing sentence", vector),
    )
    monkeypatch.setattr(
        "knowledge_browser.embedding_index.sentence_key",
        lambda _sentence: "forced-hash",
    )

    with pytest.raises(ValueError, match="embedding cache hash collision"):
        persist_embeddings(db, run, {"different sentence": vector})


def test_bulk_cache_rejects_a_different_vector_for_the_same_sentence(db, run):
    first = "[" + ",".join(["0"] * 1536) + "]"
    changed = "[" + ",".join(["1"] * 1536) + "]"
    persist_embeddings(db, run, {"same sentence": first})

    with pytest.raises(ValueError, match="embedding cache hash collision"):
        persist_embeddings(db, run, {"same sentence": changed})


def test_document_batch_is_idempotent_and_rejects_changed_chunk_content(
    db, run, tiny_dataset
):
    record = next(iter_artifacts(tiny_dataset, "jira"))
    identities = import_identities(db, tiny_dataset.context)
    embeddings = embed_missing(
        db,
        run.id,
        FakeEmbeddingClient(),
        MODEL,
        collect_sentences([record.document]),
    )

    first = write_document_batch(db, run, [record], identities, embeddings)
    second = write_document_batch(db, run, [record], identities, embeddings)

    assert first.source == "jira"
    assert first.documents == 1
    assert first.chunks > 0
    assert first.sentences > 0
    assert (second.documents, second.chunks, second.sentences) == (0, 0, 0)
    assert db.execute("SELECT count(*) FROM documents").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM sentences").fetchone() == (
        first.sentences,
    )

    field = next(
        name for name, texts in record.document.fields.items() if texts
    )
    changed_fields = {**record.document.fields}
    changed_fields[field] = [
        record.document.fields[field][0] + " changed",
        *record.document.fields[field][1:],
    ]
    changed = replace(
        record,
        document=replace(record.document, fields=changed_fields),
    )
    changed_embeddings = {
        **embeddings,
        **{
            sentence: VECTOR
            for sentence in collect_sentences([changed.document])
        },
    }

    with pytest.raises(ValueError, match="chunk content hash conflict"):
        write_document_batch(
            db, run, [changed], identities, changed_embeddings
        )


def test_resume_finishes_without_duplicates_or_repeat_provider_work(
    connection_factory, tiny_dataset
):
    partial = run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=2,
        embedding_batch_size=3,
        stop_after_batches=1,
    )

    assert partial.complete is False
    assert len(partial.batches) == 1

    final = run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=2,
        embedding_batch_size=3,
    )

    assert final.complete is True
    assert final.run_id == partial.run_id
    requested_sentences = [
        sentence
        for _, request in FakeEmbeddingClient.requests
        for sentence in request
    ]
    assert len(requested_sentences) == len(set(requested_sentences))
    with connection_factory() as conn:
        duplicates = conn.execute(
            """
            SELECT source, external_id
            FROM documents
            GROUP BY source, external_id
            HAVING count(*) > 1
            """
        ).fetchall()
        assert duplicates == []
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (3,)
        assert load_progress(conn, partial.run_id, "jira").next_line == 4


def test_progress_callback_runs_after_commit_and_failure_resumes_safely(
    connection_factory, tiny_dataset, monkeypatch
):
    committed = []
    clock = iter((10.0, 11.25, 20.0, 21.5, 23.0))
    monkeypatch.setattr(bulk_import, "monotonic", lambda: next(clock))

    def fail_after_first_commit(report):
        with connection_factory() as conn:
            progress = conn.execute(
                """
                SELECT next_line, documents
                FROM bulk_import_progress
                WHERE source = %s
                """,
                (report.source,),
            ).fetchone()
            documents = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        committed.append(
            (
                report.source,
                report.next_line,
                report.elapsed_seconds,
                progress,
                documents,
            )
        )
        raise RuntimeError("progress output stopped")

    with pytest.raises(RuntimeError, match="progress output stopped"):
        run_import(
            connection_factory,
            tiny_dataset,
            FakeEmbeddingClient,
            document_batch_size=1,
            progress_callback=fail_after_first_commit,
        )

    resumed_reports = []
    final = run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=1,
        progress_callback=resumed_reports.append,
    )

    assert committed == [("jira", 2, 1.25, (2, 1), 1)]
    assert [
        (
            report.source,
            report.next_line,
            report.elapsed_seconds,
            report.sentences_per_second > 0,
            report.estimated_remaining_seconds,
        )
        for report in resumed_reports
    ] == [
        ("jira", 3, 1.5, True, 1.5),
        ("jira", 4, 3.0, True, 0.0),
    ]
    assert final.complete is True
    requested_sentences = [
        sentence
        for _, request in FakeEmbeddingClient.requests
        for sentence in request
    ]
    assert len(requested_sentences) == len(set(requested_sentences))


def test_concurrent_runs_do_not_repeat_provider_work_or_move_progress_backward(
    connection_factory, tiny_dataset
):
    first_provider_started = Event()
    second_provider_started = Event()
    release_first_provider = Event()
    request_lock = Lock()

    class CoordinatedClient:
        requests = []

        def __init__(self):
            self.embeddings = self

        def create(self, *, model, input):
            with request_lock:
                request_index = len(type(self).requests)
                type(self).requests.append((model, tuple(input)))
            if request_index == 0:
                first_provider_started.set()
                if not release_first_provider.wait(5):
                    raise TimeoutError("test provider was not released")
            else:
                second_provider_started.set()
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=index, embedding=VECTOR)
                    for index, _ in enumerate(input)
                ]
            )

    def import_one(batch_size):
        return run_import(
            connection_factory,
            tiny_dataset,
            CoordinatedClient,
            document_batch_size=batch_size,
            work_window_size=1,
            stop_after_batches=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(import_one, 1)
        assert first_provider_started.wait(5)
        second = executor.submit(import_one, 2)
        if second_provider_started.wait(2):
            second.result(timeout=5)
        release_first_provider.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    requested_sentences = [
        sentence
        for _, request in CoordinatedClient.requests
        for sentence in request
    ]
    assert len(requested_sentences) == len(set(requested_sentences))
    assert first_result.run_id == second_result.run_id
    with connection_factory() as conn:
        progress = load_progress(conn, first_result.run_id, "jira")
        assert (progress.next_line, progress.documents) == (3, 2)
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (2,)


def test_failure_is_recorded_before_the_import_lock_is_released(
    connection_factory, tiny_dataset, monkeypatch
):
    calls = []
    original_record_failure = bulk_import._record_failure
    original_release_import_lock = bulk_import._release_import_lock

    class FailingClient:
        def __init__(self):
            self.embeddings = self

        def create(self, **_request):
            raise RuntimeError("first importer failed")

    def record_failure(*args):
        calls.append("record_failure")
        return original_record_failure(*args)

    def release_import_lock(conn):
        calls.append("release_import_lock")
        return original_release_import_lock(conn)

    monkeypatch.setattr(bulk_import, "_record_failure", record_failure)
    monkeypatch.setattr(bulk_import, "_release_import_lock", release_import_lock)

    with pytest.raises(RuntimeError, match="first importer failed"):
        run_import(
            connection_factory,
            tiny_dataset,
            FailingClient,
        )

    assert calls == ["record_failure", "release_import_lock"]
    with connection_factory() as conn:
        assert conn.execute(
            "SELECT status, safe_error FROM bulk_import_runs"
        ).fetchone() == ("failed", "batch_import_failed")


def test_cache_persists_when_document_transaction_fails_and_resume_reuses_it(
    connection_factory, tiny_dataset, monkeypatch
):
    def fail_write(*_args, **_kwargs):
        raise RuntimeError("forced batch failure")

    monkeypatch.setattr(bulk_import, "write_document_batch", fail_write)

    with pytest.raises(RuntimeError, match="forced batch failure"):
        run_import(
            connection_factory,
            tiny_dataset,
            FakeEmbeddingClient,
            document_batch_size=1,
        )

    with connection_factory() as conn:
        assert conn.execute("SELECT count(*) FROM bulk_embedding_cache").fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)
        progress = conn.execute(
            "SELECT next_line, next_offset FROM bulk_import_progress WHERE source = 'jira'"
        ).fetchone()
        assert progress == (1, 0)
        assert conn.execute(
            "SELECT status, safe_error FROM bulk_import_runs"
        ).fetchone() == ("failed", "batch_import_failed")

    requests_after_failure = list(FakeEmbeddingClient.requests)
    monkeypatch.setattr(
        bulk_import,
        "write_document_batch",
        write_document_batch,
    )

    assert run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=1,
    ).complete is True
    assert FakeEmbeddingClient.requests == requests_after_failure


def test_provider_calls_run_with_no_database_transaction(
    connection_factory, tiny_dataset
):
    importer_pids = []
    observed_states = []

    def tracked_connection_factory():
        conn = connection_factory()
        if not importer_pids:
            importer_pids.append(conn.info.backend_pid)
        return conn

    class TransactionCheckingClient(FakeEmbeddingClient):
        def create(self, **request):
            with connection_factory() as observer:
                observed_states.append(
                    observer.execute(
                        "SELECT state FROM pg_stat_activity WHERE pid = %s",
                        (importer_pids[0],),
                    ).fetchone()[0]
                )
            return super().create(**request)

    assert run_import(
        tracked_connection_factory,
        tiny_dataset,
        TransactionCheckingClient,
        document_batch_size=1,
        work_window_size=3,
    ).complete is True
    assert observed_states
    assert set(observed_states) == {"idle"}


def test_checkpoint_failure_replays_without_reembedding_or_duplicates(
    connection_factory, tiny_dataset, monkeypatch
):
    original_save_progress = bulk_import.save_progress
    failed_once = False

    def fail_first_checkpoint(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("checkpoint failed")
        return original_save_progress(*args, **kwargs)

    monkeypatch.setattr(bulk_import, "save_progress", fail_first_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        run_import(
            connection_factory,
            tiny_dataset,
            FakeEmbeddingClient,
            document_batch_size=1,
            work_window_size=3,
        )

    requests_after_failure = list(FakeEmbeddingClient.requests)
    monkeypatch.setattr(bulk_import, "save_progress", original_save_progress)
    final = run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=1,
        work_window_size=3,
    )

    assert final.complete is True
    assert FakeEmbeddingClient.requests == requests_after_failure
    with connection_factory() as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (3,)
        assert conn.execute(
            """
            SELECT count(*) FROM (
                SELECT source, external_id
                FROM documents
                GROUP BY source, external_id
                HAVING count(*) > 1
            ) AS duplicates
            """
        ).fetchone() == (0,)


def test_work_window_deduplicates_across_document_batches(
    connection_factory, tiny_dataset, monkeypatch
):
    window_sizes = []
    original_collect = bulk_import.collect_sentences

    def observed_collect(documents):
        window_sizes.append(len(documents))
        return original_collect(documents)

    monkeypatch.setattr(bulk_import, "collect_sentences", observed_collect)
    assert run_import(
        connection_factory,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=1,
        work_window_size=2,
    ).complete is True

    requested = [
        sentence
        for _, request in FakeEmbeddingClient.requests
        for sentence in request
    ]
    assert window_sizes == [2, 1]
    assert len(requested) == len(set(requested))


def test_document_conflicts_are_checked_once_per_batch(
    connection_factory, tiny_dataset
):
    document_reads = 0

    class CountingConnection:
        def __init__(self):
            self.connection = connection_factory()

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, query, params=None):
            nonlocal document_reads
            if (
                "FROM public.documents" in query
                and "external_id = ANY" in query
            ):
                document_reads += 1
            return self.connection.execute(query, params)

    assert run_import(
        CountingConnection,
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=3,
        work_window_size=3,
    ).complete is True
    assert document_reads == 1


def test_transient_provider_failure_stops_after_five_attempts_with_safe_state(
    connection_factory, tiny_dataset, monkeypatch
):
    transport_attempts = 0

    def unavailable(_request):
        nonlocal transport_attempts
        transport_attempts += 1
        return httpx.Response(
            500,
            json={"error": {"message": "secret provider payload"}},
        )

    monkeypatch.setattr("knowledge_browser.embedding_index.time.sleep", lambda _delay: None)
    http_client = httpx.Client(transport=httpx.MockTransport(unavailable))
    client = OpenAI(
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        http_client=http_client,
    )
    assert client.max_retries > 0
    try:
        with pytest.raises(RuntimeError, match="embedding provider unavailable"):
                run_import(
                    connection_factory,
                    tiny_dataset,
                    lambda: client,
                    document_batch_size=1,
                    work_window_size=1,
                )
    finally:
        client.close()

    assert transport_attempts == 5
    with connection_factory() as conn:
        assert conn.execute(
            "SELECT status, safe_error FROM bulk_import_runs"
        ).fetchone() == ("failed", "embedding_provider_failed")


@pytest.mark.parametrize("status_code", [408, 409])
def test_embedding_cache_retries_timeout_and_conflict_statuses(
    db, run, monkeypatch, status_code
):
    class StatusClient:
        def __init__(self):
            self.calls = 0
            self.embeddings = self

        def create(self, **_request):
            self.calls += 1
            if self.calls == 1:
                response = httpx.Response(
                    status_code,
                    request=httpx.Request("POST", "https://api.openai.test/embeddings"),
                )
                raise APIStatusError("retryable status", response=response, body=None)
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=VECTOR)]
            )

    client = StatusClient()
    monkeypatch.setattr("knowledge_browser.embedding_index.time.sleep", lambda _delay: None)

    assert embed_missing(db, run.id, client, MODEL, ["retry me"])["retry me"]
    assert client.calls == 2
