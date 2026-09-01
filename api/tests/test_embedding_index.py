from types import SimpleNamespace

import pytest

from knowledge_browser.embedding_index import (
    collect_sentences,
    create_embeddings,
    encoded_vector,
    sentence_key,
    sentences,
)


pytestmark = pytest.mark.unit


def test_sentences_preserves_order_and_strips_whitespace():
    assert sentences(" First sentence.  Second? Third!") == [
        "First sentence.",
        "Second?",
        "Third!",
    ]


def test_collect_sentences_deduplicates_and_excludes_issue_metadata():
    documents = [
        SimpleNamespace(fields={
            "body": ["First sentence. Shared sentence!"],
            "issue_metadata": ["Should not be embedded."],
        }),
        SimpleNamespace(fields={"body": ["Shared sentence! Last sentence?"]}),
    ]

    assert collect_sentences(documents) == [
        "First sentence.",
        "Shared sentence!",
        "Last sentence?",
    ]


def test_sentence_key_is_the_utf8_sha256_digest():
    assert sentence_key("same sentence") == (
        "935b715416c60db94100471aa0d6ccb30a9dc3b93dcab24e23e775616149ec24"
    )


def test_encoded_vector_requires_and_preserves_1536_values():
    vector = [0.0, 1.25, -2.0, *([3.0] * 1533)]

    encoded = encoded_vector(vector)

    assert encoded.startswith("[0.0,1.25,-2.0,3.0,")
    assert encoded.endswith(",3.0]")
    assert encoded.count(",") == 1535


def test_encoded_vector_rejects_invalid_dimensions():
    with pytest.raises(ValueError, match="embedding provider returned invalid dimensions"):
        encoded_vector([0.0] * 1535)


def test_batches_dedupes_and_uses_provider_indexes():
    calls = []

    class Embeddings:
        def create(self, *, model, input):
            calls.append((model, input))
            return SimpleNamespace(data=[
                SimpleNamespace(index=i, embedding=[float(len(text))] * 1536)
                for i, text in reversed(list(enumerate(input)))
            ])

    result = create_embeddings(
        SimpleNamespace(embeddings=Embeddings()),
        ["one", "two-two", "one", "three"],
        "text-embedding-3-small",
        batch_size=2,
    )

    assert calls == [
        ("text-embedding-3-small", ["one", "two-two"]),
        ("text-embedding-3-small", ["three"]),
    ]
    assert result["one"] == [3.0] * 1536
    assert result["two-two"] == [7.0] * 1536
    assert result["three"] == [5.0] * 1536


@pytest.mark.parametrize("batch_size", [0, -1])
def test_rejects_non_positive_batch_size(batch_size):
    with pytest.raises(ValueError, match="batch_size must be positive"):
        create_embeddings(
            SimpleNamespace(embeddings=None), ["text"], "model", batch_size=batch_size
        )


def test_rejects_invalid_provider_indexes():
    class Embeddings:
        def create(self, **_request):
            return SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[0.0] * 1536)])

    with pytest.raises(ValueError, match="embedding provider returned invalid indexes"):
        create_embeddings(SimpleNamespace(embeddings=Embeddings()), ["text"], "model")


def test_rejects_duplicate_provider_indexes():
    class Embeddings:
        def create(self, **_request):
            return SimpleNamespace(data=[
                SimpleNamespace(index=0, embedding=[0.0] * 1536),
                SimpleNamespace(index=1, embedding=[1.0] * 1536),
                SimpleNamespace(index=1, embedding=[2.0] * 1536),
            ])

    with pytest.raises(ValueError, match="embedding provider returned invalid indexes"):
        create_embeddings(
            SimpleNamespace(embeddings=Embeddings()), ["first", "second"], "model"
        )


def test_rejects_invalid_provider_vector_size():
    class Embeddings:
        def create(self, **_request):
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0] * 2)])

    with pytest.raises(ValueError, match="embedding provider returned invalid dimensions"):
        create_embeddings(SimpleNamespace(embeddings=Embeddings()), ["text"], "model")
