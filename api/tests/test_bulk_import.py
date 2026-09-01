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
    sentence_key,
)


DATA = Path(__file__).parents[2] / "data" / "company"
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
        manifest={"dataset_version": "tiny-v1"},
        manifest_digest="tiny-manifest",
        context=validated_company.context,
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
        assert (progress.next_line, progress.documents) == (4, 3)
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (3,)


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


def test_cache_rows_documents_and_progress_roll_back_together(
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
        assert conn.execute("SELECT count(*) FROM bulk_embedding_cache").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)
        progress = conn.execute(
            "SELECT next_line, next_offset FROM bulk_import_progress WHERE source = 'jira'"
        ).fetchone()
        assert progress == (1, 0)
        assert conn.execute(
            "SELECT status, safe_error FROM bulk_import_runs"
        ).fetchone() == ("failed", "batch_import_failed")


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
