import json

import pytest

from knowledge_browser.eval_query_embeddings import (
    DIMENSIONS,
    QueryEmbeddingError,
    load_query_embeddings,
    query_key,
    read_cache,
)


pytestmark = pytest.mark.unit


QUERIES = [
    {"id": "q1", "query": "first question"},
    {"id": "q2", "query": "second question"},
    {"id": "q1-denied", "query": "first question"},
]


def _embedder(calls):
    def embed(texts, model):
        calls.append(list(texts))
        return [[float(index + 1)] * DIMENSIONS for index in range(len(texts))]

    return embed


def test_missing_queries_are_requested_once_and_cached(tmp_path):
    path = tmp_path / "vectors.json"
    calls = []

    first = load_query_embeddings(path, QUERIES, _embedder(calls))

    assert calls == [["first question", "second question"]]
    assert set(first) == {"first question", "second question"}
    assert first["first question"] == [1.0] * DIMENSIONS

    later = []
    second = load_query_embeddings(path, QUERIES, _embedder(later))

    assert later == []
    assert second == first


def test_only_the_edited_query_is_re_requested(tmp_path):
    path = tmp_path / "vectors.json"
    load_query_embeddings(path, QUERIES, _embedder([]))
    calls = []

    edited = [{"id": "q1", "query": "first question, reworded"}, QUERIES[1]]
    load_query_embeddings(path, edited, _embedder(calls))

    assert calls == [["first question, reworded"]]
    assert query_key("first question") in read_cache(path)


def test_a_different_model_invalidates_the_whole_cache(tmp_path):
    path = tmp_path / "vectors.json"
    load_query_embeddings(path, QUERIES, _embedder([]))
    calls = []

    load_query_embeddings(path, QUERIES, _embedder(calls), model="other-model")

    assert calls == [["first question", "second question"]]
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "other-model"


def test_requests_can_be_refused_so_a_gate_never_runs_on_a_partial_cache(tmp_path):
    path = tmp_path / "vectors.json"

    with pytest.raises(QueryEmbeddingError, match="missing"):
        load_query_embeddings(path, QUERIES, _embedder([]), allow_requests=False)


def test_wrong_dimensions_are_rejected_instead_of_stored(tmp_path):
    path = tmp_path / "vectors.json"

    with pytest.raises(QueryEmbeddingError, match="dimensions"):
        load_query_embeddings(path, QUERIES, lambda texts, model: [[0.0] * 8] * len(texts))

    assert read_cache(path) == {}


def test_a_short_provider_response_is_rejected(tmp_path):
    path = tmp_path / "vectors.json"

    with pytest.raises(QueryEmbeddingError, match="count"):
        load_query_embeddings(path, QUERIES, lambda texts, model: [[0.0] * DIMENSIONS])
