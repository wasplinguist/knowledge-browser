import time
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import format_datetime
from threading import Event, Lock
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from knowledge_browser.embedding_index import (
    EmbeddingProviderError,
    EmbeddingRequestConfig,
    collect_sentences,
    create_embeddings,
    encoded_vector,
    request_missing_embeddings,
    sentence_key,
    sentences,
    token_batches,
)


pytestmark = pytest.mark.unit
MODEL = "text-embedding-3-small"
VECTOR = [0.0] * 1536


def _config(**changes):
    return replace(
        EmbeddingRequestConfig(
            concurrency=2,
            max_inputs=2,
            max_estimated_tokens=100,
            connect_timeout=1.0,
            read_timeout=2.0,
            write_timeout=3.0,
            total_timeout=10.0,
        ),
        **changes,
    )


def _response(texts, *, indexes=None, dimensions=1536):
    indexes = range(len(texts)) if indexes is None else indexes
    return SimpleNamespace(data=[
        SimpleNamespace(
            index=index,
            embedding=[float(len(texts[position]))] * dimensions,
        )
        for position, index in enumerate(indexes)
    ])


class FakeEmbeddingClient:
    def __init__(self, handler=None):
        self.embeddings = self
        self.handler = handler or _response
        self.inputs = []
        self.options = []

    def with_options(self, **options):
        self.options.append(options)
        return self

    def create(self, *, model, input):
        assert model == MODEL
        texts = tuple(input)
        self.inputs.append(texts)
        return self.handler(texts)


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


def test_token_batches_are_deterministic_and_respect_both_limits():
    assert token_batches(
        ["aaaa", "bbbb", "cccc"], max_inputs=2, max_estimated_tokens=8
    ) == (("aaaa", "bbbb"), ("cccc",))
    assert token_batches(
        ["a" * 9, "b" * 9], max_inputs=2, max_estimated_tokens=5
    ) == (("a" * 9,), ("b" * 9,))


def test_token_batches_reject_an_oversized_single_input():
    with pytest.raises(ValueError, match="estimated token limit"):
        token_batches(["a" * 17], max_inputs=2, max_estimated_tokens=4)


def test_concurrent_responses_restore_input_order_and_encode_vectors():
    def delayed_response(texts):
        time.sleep({"one": 0.03, "two": 0.02, "three": 0.01}[texts[0]])
        return _response(texts)

    result = request_missing_embeddings(
        FakeEmbeddingClient(delayed_response),
        MODEL,
        ["one", "two", "three"],
        _config(concurrency=3, max_inputs=1),
    )

    assert list(result.vectors) == ["one", "two", "three"]
    assert result.vectors["one"] == "[" + ",".join(["3.0"] * 1536) + "]"
    assert result.provider_requests == 3
    assert result.retries == 0
    assert result.concurrency == 3


def test_duplicate_sentences_are_one_provider_input():
    client = FakeEmbeddingClient()

    result = request_missing_embeddings(
        client, MODEL, ["same", "same"], _config()
    )

    assert client.inputs == [("same",)]
    assert list(result.vectors) == ["same"]


def test_scheduler_never_exceeds_configured_concurrency():
    lock = Lock()
    active = 0
    maximum_active = 0

    def observed_response(texts):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return _response(texts)

    request_missing_embeddings(
        FakeEmbeddingClient(observed_response),
        MODEL,
        [str(index) for index in range(6)],
        _config(concurrency=3, max_inputs=1),
    )

    assert maximum_active == 3


@pytest.mark.parametrize("concurrency", [0, 17])
def test_scheduler_rejects_concurrency_outside_the_hard_bound(concurrency):
    with pytest.raises(ValueError, match="concurrency must be between 1 and 16"):
        _config(concurrency=concurrency)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"max_inputs": 2049}, "max_inputs must be between 1 and 2048"),
        (
            {"max_estimated_tokens": 300_001},
            "max_estimated_tokens must be between 1 and 300000",
        ),
    ],
)
def test_scheduler_rejects_request_sizes_outside_hard_bounds(change, message):
    with pytest.raises(ValueError, match=message):
        _config(**change)


@pytest.mark.parametrize(
    ("indexes", "dimensions", "message"),
    [
        ([1], 1536, "invalid indexes"),
        ([0], 2, "invalid dimensions"),
    ],
)
def test_scheduler_rejects_invalid_provider_responses(indexes, dimensions, message):
    client = FakeEmbeddingClient(
        lambda texts: _response(texts, indexes=indexes, dimensions=dimensions)
    )

    with pytest.raises(ValueError, match=message):
        request_missing_embeddings(client, MODEL, ["text"], _config())

    assert len(client.inputs) == 1


def _status_error(status_code, *, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    response = httpx.Response(
        status_code,
        headers=headers,
        request=httpx.Request("POST", "https://api.openai.test/embeddings"),
    )
    return APIStatusError("provider status", response=response, body=None)


@pytest.mark.parametrize(
    "first_error",
    [
        TimeoutError("timed out"),
        ConnectionResetError("connection reset"),
        _status_error(408),
        _status_error(409),
        _status_error(503),
    ],
)
def test_scheduler_retries_only_transient_failures(monkeypatch, first_error):
    attempts = 0

    def fail_once(texts):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise first_error
        return _response(texts)

    monkeypatch.setattr(
        "knowledge_browser.embedding_index._wait_for_retry", lambda *_: False
    )
    result = request_missing_embeddings(
        FakeEmbeddingClient(fail_once), MODEL, ["text"], _config()
    )

    assert attempts == 2
    assert result.provider_requests == 2
    assert result.retries == 1


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [("0.75", 0.75), ("100", 2.0)],
)
def test_scheduler_honors_retry_after(monkeypatch, retry_after, expected_delay):
    attempts = 0
    delays = []

    def rate_limited_once(texts):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _status_error(429, retry_after=retry_after)
        return _response(texts)

    monkeypatch.setattr("knowledge_browser.embedding_index.random.uniform", lambda *_: 0)
    monkeypatch.setattr(
        "knowledge_browser.embedding_index._wait_for_retry",
        lambda _event, delay: delays.append(delay) or False,
    )
    result = request_missing_embeddings(
        FakeEmbeddingClient(rate_limited_once), MODEL, ["text"], _config()
    )

    assert delays == [expected_delay]
    assert result.provider_requests == 2
    assert result.retries == 1


def test_scheduler_honors_http_date_retry_after(monkeypatch):
    attempts = 0
    delays = []
    retry_after = format_datetime(
        datetime.fromtimestamp(1_001, tz=timezone.utc), usegmt=True
    )

    def rate_limited_once(texts):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _status_error(429, retry_after=retry_after)
        return _response(texts)

    monkeypatch.setattr("knowledge_browser.embedding_index.time.time", lambda: 1_000)
    monkeypatch.setattr("knowledge_browser.embedding_index.random.uniform", lambda *_: 0)
    monkeypatch.setattr(
        "knowledge_browser.embedding_index._wait_for_retry",
        lambda _event, delay: delays.append(delay) or False,
    )
    request_missing_embeddings(
        FakeEmbeddingClient(rate_limited_once), MODEL, ["text"], _config()
    )

    assert delays == [1.0]


def test_scheduler_stops_after_five_real_requests(monkeypatch):
    attempts = 0

    def always_times_out(_texts):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "knowledge_browser.embedding_index._wait_for_retry", lambda *_: False
    )
    with pytest.raises(EmbeddingProviderError, match="unavailable after retries"):
        request_missing_embeddings(
            FakeEmbeddingClient(always_times_out), MODEL, ["text"], _config()
        )

    assert attempts == 5


def test_scheduler_does_not_retry_terminal_provider_errors(monkeypatch):
    attempts = 0

    def invalid_request(_texts):
        nonlocal attempts
        attempts += 1
        raise _status_error(400)

    monkeypatch.setattr(
        "knowledge_browser.embedding_index._wait_for_retry",
        lambda *_: pytest.fail("terminal errors must not back off"),
    )
    with pytest.raises(APIStatusError):
        request_missing_embeddings(
            FakeEmbeddingClient(invalid_request), MODEL, ["text"], _config()
        )

    assert attempts == 1


def test_scheduler_cancels_queued_batches_after_terminal_invalid_response():
    client = FakeEmbeddingClient(
        lambda texts: _response(texts, indexes=[1], dimensions=1536)
    )

    with pytest.raises(ValueError, match="invalid indexes"):
        request_missing_embeddings(
            client,
            MODEL,
            ["first", "second", "third"],
            _config(concurrency=1, max_inputs=1),
        )

    assert client.inputs == [("first",)]


def test_scheduler_interrupts_retry_backoff_after_terminal_invalid_response():
    retry_started = Event()

    def coordinated_response(texts):
        if texts == ("retry",):
            retry_started.set()
            raise TimeoutError("retry later")
        assert retry_started.wait(1.0)
        time.sleep(0.02)
        return _response(texts, indexes=[1])

    started = time.monotonic()
    with pytest.raises(ValueError, match="invalid indexes"):
        request_missing_embeddings(
            FakeEmbeddingClient(coordinated_response),
            MODEL,
            ["retry", "invalid"],
            _config(concurrency=2, max_inputs=1),
        )

    assert time.monotonic() - started < 0.15


def test_scheduler_disables_sdk_retries_and_sets_explicit_timeouts():
    client = FakeEmbeddingClient()

    request_missing_embeddings(client, MODEL, ["text"], _config())

    assert len(client.options) == 1
    assert client.options[0]["max_retries"] == 0
    timeout = client.options[0]["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
        1.0,
        2.0,
        3.0,
        1.0,
    )


def test_scheduler_clamps_sdk_timeouts_to_the_batch_total_deadline(monkeypatch):
    client = FakeEmbeddingClient()
    moments = iter([100.0, 100.01, 100.02])
    monkeypatch.setattr(
        "knowledge_browser.embedding_index.time.monotonic", lambda: next(moments)
    )

    request_missing_embeddings(
        client, MODEL, ["text"], _config(total_timeout=0.05)
    )

    timeout = client.options[0]["timeout"]
    assert max(
        timeout.connect, timeout.read, timeout.write, timeout.pool
    ) == pytest.approx(0.04)


def test_scheduler_hard_deadline_does_not_wait_for_a_stuck_transport():
    release = Event()

    def stuck_response(texts):
        release.wait(1.0)
        return _response(texts)

    started = time.monotonic()
    try:
        with pytest.raises(EmbeddingProviderError, match="total timeout"):
            request_missing_embeddings(
                FakeEmbeddingClient(stuck_response),
                MODEL,
                ["text"],
                _config(total_timeout=0.05),
            )
        assert time.monotonic() - started < 0.25
    finally:
        release.set()
