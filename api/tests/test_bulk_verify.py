import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

import knowledge_browser.bulk_verify as bulk_verify_module
from conftest import _prepare_test_database, _seed
from knowledge_browser.bulk_import import (
    finalize_indexes,
    prepare_bulk_load,
    run_import,
)
from knowledge_browser.bulk_verify import verify_redwood
from knowledge_browser.dataset import (
    SOURCES,
    ValidatedDataset,
    validate_streaming_dataset,
)
from knowledge_browser.profiles import load_profile


pytestmark = pytest.mark.integration

DATA = Path(__file__).parents[2] / "data" / "company"
RELEASED = Path(__file__).parents[2] / "search" / "profiles" / "released.json"
VECTOR = [0.0] * 1536


class FakeEmbeddingClient:
    def __init__(self):
        self.inputs = []
        self.embeddings = self

    def create(self, *, model, input):
        self.inputs.extend(input)
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
        first = source.readline()
    for source in SOURCES:
        (artifacts / f"{source}.jsonl").write_bytes(
            first if source == "jira" else b""
        )
    return ValidatedDataset(
        root=tmp_path,
        manifest={"dataset_version": "tiny-v1", "counts": {"artifacts": 1}},
        manifest_digest="tiny-finalize-manifest",
        context=validated_company.context,
    )


@pytest.fixture
def clean_database_url(request):
    url = _prepare_test_database()
    request.addfinalizer(_prepare_test_database)
    return url


def _verification_data(tmp_path, source_counts=None):
    source_counts = source_counts or {
        "confluence": 1,
        "github": 1,
        "jira": 5,
        "slack": 1,
    }
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for source, count in source_counts.items():
        (artifacts / f"{source}.jsonl").write_text("{}\n" * count)
    (tmp_path / "qa.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "question_id": "qst-test-company",
                    "question": "Company",
                    "expected_doc_ids": ["COMPANY-1"],
                    "required_group_ids": [],
                    "source_types": ["jira"],
                },
                {
                    "question_id": "qst-test-group",
                    "question": "Group",
                    "expected_doc_ids": ["GROUP-1"],
                    "required_group_ids": ["engineering"],
                    "source_types": ["slack"],
                },
            )
        )
        + "\n"
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_version": "test-v1",
                "counts": {
                    "artifacts": 8,
                    "companies": 1,
                    "employees": 4,
                    "incidents": 0,
                    "projects": 0,
                    "qa": 2,
                    "teams": 1,
                }
            }
        )
    )
    return tmp_path


def _verification_database(url, data_dir):
    with psycopg.connect(url) as conn:
        _seed(conn)
        conn.execute(
            "UPDATE sentences SET embedding_model = 'text-embedding-3-small'"
        )
        conn.execute(
            "UPDATE groups SET raw_payload = '{\"acl_group_id\": \"engineering\"}'"
        )
        run_id = _run(conn, "complete")
        conn.execute(
            "UPDATE bulk_import_runs SET manifest_digest = %s WHERE id = %s",
            (
                hashlib.sha256(
                    (data_dir / "manifest.json").read_bytes()
                ).hexdigest(),
                run_id,
            ),
        )
        for source, count in {
            "confluence": 1,
            "github": 1,
            "jira": 5,
            "slack": 1,
        }.items():
            conn.execute(
                """
                INSERT INTO bulk_import_progress (
                  run_id, source, next_line, documents, chunks, sentences
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, source, count + 1, count, count, count),
            )
    return lambda: psycopg.connect(url)


def _run(db, status="indexing"):
    run_id = uuid4()
    db.execute(
        """
        INSERT INTO bulk_import_runs (
          id, manifest_digest, dataset_version, embedding_model,
          embedding_dimensions, status
        ) VALUES (%s, %s, 'test-v1', 'text-embedding-3-small', 1536, %s)
        """,
        (run_id, str(run_id), status),
    )
    return run_id


def test_finalize_creates_indexes_and_marks_complete(db):
    run_id = _run(db)
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute("DROP INDEX sentences_embedding_idx")

    finalize_indexes(db, run_id)

    names = {
        row[0]
        for row in db.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        )
    }
    assert {"chunks_fts_idx", "sentences_embedding_idx"} <= names
    assert db.execute(
        "SELECT status, safe_error, completed_at IS NOT NULL "
        "FROM bulk_import_runs WHERE id = %s",
        (run_id,),
    ).fetchone() == ("complete", None, True)


def test_prepare_bulk_load_drops_only_deferred_search_indexes(db):
    prepare_bulk_load(db)

    names = {
        row[0]
        for row in db.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        )
    }
    assert "chunks_fts_idx" not in names
    assert "sentences_embedding_idx" not in names
    assert "documents_pkey" in names


def test_finalize_leaves_run_indexing_when_an_index_is_invalid(db):
    run_id = _run(db)
    db.execute("DROP INDEX chunks_fts_idx")
    db.execute("CREATE INDEX chunks_fts_idx ON chunks USING gin (metadata)")

    with pytest.raises(
        RuntimeError, match="indexes failed compatibility checks"
    ):
        finalize_indexes(db, run_id)

    assert db.execute(
        "SELECT status, completed_at FROM bulk_import_runs WHERE id = %s",
        (run_id,),
    ).fetchone() == ("indexing", None)


def test_run_import_finalizes_indexes_before_reporting_complete(
    tiny_dataset, clean_database_url
):
    url = clean_database_url
    with psycopg.connect(url) as conn:
        prepare_bulk_load(conn)

    result = run_import(
        lambda: psycopg.connect(url),
        tiny_dataset,
        FakeEmbeddingClient,
        document_batch_size=1,
    )

    assert result.complete is True
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT status FROM bulk_import_runs WHERE id = %s", (result.run_id,)
        ).fetchone() == ("complete",)
        assert conn.execute(
            "SELECT count(*) FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND indexname IN ('chunks_fts_idx', 'sentences_embedding_idx')"
        ).fetchone() == (2,)


def test_run_import_refuses_a_checkpoint_that_skips_source_rows(
    tiny_dataset, clean_database_url
):
    url = clean_database_url
    with psycopg.connect(url) as conn:
        prepare_bulk_load(conn)
    partial = run_import(
        lambda: psycopg.connect(url),
        tiny_dataset,
        FakeEmbeddingClient,
        stop_after_batches=0,
    )
    jira_size = (tiny_dataset.root / "artifacts" / "jira.jsonl").stat().st_size
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            UPDATE bulk_import_progress
            SET next_line = 1, next_offset = %s, documents = 0
            WHERE run_id = %s AND source = 'jira'
            """,
            (jira_size, partial.run_id),
        )

    with pytest.raises(RuntimeError, match="source checkpoint is incomplete"):
        run_import(
            lambda: psycopg.connect(url),
            tiny_dataset,
            FakeEmbeddingClient,
        )

    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT status FROM bulk_import_runs WHERE id = %s", (partial.run_id,)
        ).fetchone() == ("failed",)


def test_verify_blocks_unknown_user_and_scores_unchanged_qa(
    tmp_path, clean_database_url
):
    data_dir = _verification_data(tmp_path)
    client = FakeEmbeddingClient()

    report = verify_redwood(
        _verification_database(clean_database_url, data_dir),
        data_dir,
        client,
        load_profile(RELEASED),
    )

    assert report.compatible is True
    assert report.counts == {
        "users": 4,
        "groups": 1,
        "documents": 8,
        "chunks": 8,
        "sentences": 8,
        "embedded_sentences": 8,
        "wrong_embedding_model": 0,
    }
    assert report.sources == {
        "confluence": 1,
        "github": 1,
        "jira": 5,
        "slack": 1,
    }
    assert report.missing_embeddings == 0
    assert report.acl_checks == {
        "company_visible": True,
        "group_visible": True,
        "group_unauthorized_results": 0,
        "direct_user_visible": True,
        "direct_unauthorized_results": 0,
        "unknown_user_results": 0,
    }
    assert report.recall_at_10 == 1.0
    assert report.mrr == 1.0
    assert report.p50_ms >= 0
    assert report.p95_ms >= report.p50_ms
    assert client.inputs == [
        "Company",
        "Group",
        "Company body",
        "Group body",
        "Direct body",
    ]


def test_verify_acl_probes_use_released_hybrid_search(
    tmp_path, clean_database_url, monkeypatch
):
    data_dir = _verification_data(tmp_path)
    real_search = bulk_verify_module.hybrid_search
    calls = []

    def recording_search(conn, user_id, query, embedding, **kwargs):
        results = real_search(conn, user_id, query, embedding, **kwargs)
        calls.append(
            (
                query,
                user_id,
                {item["external_id"] for item in results},
                kwargs["profile"].name,
            )
        )
        return results

    monkeypatch.setattr(bulk_verify_module, "hybrid_search", recording_search)

    report = verify_redwood(
        _verification_database(clean_database_url, data_dir),
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    protected = {
        "Company body": "COMPANY-1",
        "Group body": "GROUP-1",
        "Direct body": "DIRECT-1",
    }
    for query, expected_id in protected.items():
        probes = [call for call in calls if call[0] == query]
        assert probes
        assert all(call[3] == "released" for call in probes)
        assert any(expected_id in call[2] for call in probes)
        assert any(expected_id not in call[2] for call in probes)

    assert report.acl_checks == {
        "company_visible": True,
        "group_visible": True,
        "group_unauthorized_results": 0,
        "direct_user_visible": True,
        "direct_unauthorized_results": 0,
        "unknown_user_results": 0,
    }


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected_compatible"),
    ((2.0, True), (2.000001, False)),
)
def test_verify_enforces_two_second_p95_boundary(
    tmp_path,
    clean_database_url,
    monkeypatch,
    elapsed_seconds,
    expected_compatible,
):
    data_dir = _verification_data(tmp_path)
    clock = iter((0.0, elapsed_seconds, 3.0, 3.0 + elapsed_seconds))
    monkeypatch.setattr(
        bulk_verify_module.time, "perf_counter", lambda: next(clock)
    )

    report = verify_redwood(
        _verification_database(clean_database_url, data_dir),
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    assert report.p95_ms == pytest.approx(elapsed_seconds * 1000)
    assert report.compatible is expected_compatible


def test_verify_detects_exact_source_count_mismatch(tmp_path, clean_database_url):
    data_dir = _verification_data(
        tmp_path,
        {"confluence": 2, "github": 1, "jira": 4, "slack": 1},
    )

    report = verify_redwood(
        _verification_database(clean_database_url, data_dir),
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    assert report.compatible is False
    assert sum(report.sources.values()) == 8


def test_verify_counts_missing_embeddings(tmp_path, clean_database_url):
    data_dir = _verification_data(tmp_path)
    factory = _verification_database(clean_database_url, data_dir)
    with factory() as conn:
        conn.execute(
            "UPDATE sentences SET embedding = NULL "
            "WHERE source = 'jira' AND chunk_id = 'jira:COMPANY-1:0'"
        )

    report = verify_redwood(
        factory,
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    assert report.compatible is False
    assert report.missing_embeddings == 1


def test_verify_detects_progress_count_mismatch(tmp_path, clean_database_url):
    data_dir = _verification_data(tmp_path)
    factory = _verification_database(clean_database_url, data_dir)
    with factory() as conn:
        conn.execute(
            "UPDATE bulk_import_progress SET chunks = 999 WHERE source = 'jira'"
        )

    report = verify_redwood(
        factory,
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    assert report.compatible is False


def test_verify_fails_when_a_qa_group_cannot_be_resolved(
    tmp_path, clean_database_url
):
    data_dir = _verification_data(tmp_path)
    factory = _verification_database(clean_database_url, data_dir)
    with factory() as conn:
        conn.execute("UPDATE groups SET raw_payload = '{}'")

    report = verify_redwood(
        factory,
        data_dir,
        FakeEmbeddingClient(),
        load_profile(RELEASED),
    )

    assert report.compatible is False
