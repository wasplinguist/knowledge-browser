from __future__ import annotations

import hashlib
import math
import random
import re
import time
from collections.abc import Sequence
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import batched
from threading import Event

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError


MAX_EMBEDDING_CONCURRENCY = 16
MAX_PROVIDER_REQUESTS = 5
MAX_RETRY_DELAY = 2.0


class EmbeddingProviderError(RuntimeError):
    safe_code = "embedding_provider_failed"


class EmbeddingResponseError(ValueError):
    safe_code = "embedding_provider_invalid_response"


@dataclass(frozen=True)
class EmbeddingRequestConfig:
    concurrency: int = 4
    max_inputs: int = 100
    max_estimated_tokens: int = 20_000
    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    write_timeout: float = 30.0
    total_timeout: float = 120.0

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= MAX_EMBEDDING_CONCURRENCY:
            raise ValueError("concurrency must be between 1 and 16")
        if self.max_inputs <= 0:
            raise ValueError("max_inputs must be positive")
        if self.max_estimated_tokens <= 0:
            raise ValueError("max_estimated_tokens must be positive")
        for name in (
            "connect_timeout",
            "read_timeout",
            "write_timeout",
            "total_timeout",
        ):
            value = getattr(self, name)
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class EmbeddingRequestResult:
    vectors: dict[str, str]
    provider_requests: int
    retries: int
    concurrency: int


def _estimated_tokens(sentence: str) -> int:
    return max(1, math.ceil(len(sentence.encode("utf-8")) / 4))


def token_batches(
    sentences: Sequence[str], max_inputs: int, max_estimated_tokens: int
) -> tuple[tuple[str, ...], ...]:
    if max_inputs <= 0:
        raise ValueError("max_inputs must be positive")
    if max_estimated_tokens <= 0:
        raise ValueError("max_estimated_tokens must be positive")

    result = []
    batch = []
    batch_tokens = 0
    for sentence in sentences:
        estimated_tokens = _estimated_tokens(sentence)
        if estimated_tokens > max_estimated_tokens:
            raise ValueError("embedding input exceeds estimated token limit")
        if batch and (
            len(batch) == max_inputs
            or batch_tokens + estimated_tokens > max_estimated_tokens
        ):
            result.append(tuple(batch))
            batch = []
            batch_tokens = 0
        batch.append(sentence)
        batch_tokens += estimated_tokens
    if batch:
        result.append(tuple(batch))
    return tuple(result)


def sentences(text):
    return [part.strip() for part in re.findall(r"[^.!?]+[.!?]?", text) if part.strip()]


def collect_sentences(documents):
    values = []
    for document in documents:
        for field, texts in document.fields.items():
            if field == "issue_metadata":
                continue
            for text in filter(None, texts):
                values.extend(sentences(text))
    return list(dict.fromkeys(values))


def sentence_key(sentence: str) -> str:
    return hashlib.sha256(sentence.encode()).hexdigest()


def _validate_dimensions(vector) -> None:
    try:
        valid = len(vector) == 1536
    except TypeError as error:
        raise EmbeddingResponseError(
            "embedding provider returned invalid dimensions"
        ) from error
    if not valid:
        raise EmbeddingResponseError("embedding provider returned invalid dimensions")


def encoded_vector(vector: Sequence[float]) -> str:
    _validate_dimensions(vector)
    return "[" + ",".join(map(str, vector)) + "]"


def _vectors(response, size: int):
    try:
        items = response.data
        indexes = [item.index for item in items]
    except (AttributeError, TypeError) as error:
        raise EmbeddingResponseError(
            "embedding provider returned invalid indexes"
        ) from error
    if (
        len(items) != size
        or len(set(indexes)) != len(items)
        or set(indexes) != set(range(size))
    ):
        raise EmbeddingResponseError("embedding provider returned invalid indexes")
    by_index = {item.index: item.embedding for item in items}
    try:
        return [by_index[index] for index in range(size)]
    except (KeyError, TypeError) as error:
        raise EmbeddingResponseError(
            "embedding provider returned invalid indexes"
        ) from error


def _transient(error: Exception) -> bool:
    if isinstance(
        error,
        (
            TimeoutError,
            ConnectionError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
        ),
    ):
        return True
    return isinstance(error, APIStatusError) and (
        error.status_code in {408, 409, 429} or error.status_code >= 500
    )


def _retry_after(error: Exception) -> float | None:
    if not isinstance(error, APIStatusError):
        return None
    value = error.response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if delay < 0 or not math.isfinite(delay):
        return None
    return min(delay, MAX_RETRY_DELAY)


def _retry_delay(error: Exception, attempt: int) -> float:
    base = min(0.25 * 2**attempt, MAX_RETRY_DELAY)
    backoff = min(base + random.uniform(0, base * 0.25), MAX_RETRY_DELAY)
    return max(backoff, _retry_after(error) or 0.0)


def _configured_client(client, config: EmbeddingRequestConfig):
    with_options = getattr(client, "with_options", None)
    if not callable(with_options):
        return client
    return with_options(
        max_retries=0,
        timeout=httpx.Timeout(
            config.read_timeout,
            connect=config.connect_timeout,
            read=config.read_timeout,
            write=config.write_timeout,
            pool=config.connect_timeout,
        ),
    )


def _request_batch(client, model, batch, config, cancelled):
    deadline = time.monotonic() + config.total_timeout
    for attempt in range(MAX_PROVIDER_REQUESTS):
        if cancelled.is_set():
            raise CancelledError
        if time.monotonic() >= deadline:
            cancelled.set()
            raise EmbeddingProviderError(
                "embedding provider unavailable after retries"
            )
        try:
            response = client.embeddings.create(model=model, input=batch)
            if time.monotonic() > deadline:
                raise EmbeddingProviderError(
                    "embedding provider exceeded total timeout"
                )
            vectors = tuple(
                encoded_vector(vector) for vector in _vectors(response, len(batch))
            )
            return vectors, attempt + 1
        except Exception as error:
            if isinstance(error, EmbeddingProviderError):
                cancelled.set()
                raise
            if not _transient(error):
                cancelled.set()
                raise
            if attempt == MAX_PROVIDER_REQUESTS - 1:
                cancelled.set()
                raise EmbeddingProviderError(
                    "embedding provider unavailable after retries"
                ) from error
            delay = _retry_delay(error, attempt)
            if delay >= deadline - time.monotonic():
                cancelled.set()
                raise EmbeddingProviderError(
                    "embedding provider unavailable after retries"
                ) from error
            time.sleep(delay)
    raise AssertionError("unreachable")


def request_missing_embeddings(
    client,
    model: str,
    sentences: Sequence[str],
    config: EmbeddingRequestConfig,
) -> EmbeddingRequestResult:
    unique = tuple(dict.fromkeys(sentences))
    batches = token_batches(unique, config.max_inputs, config.max_estimated_tokens)
    if not batches:
        return EmbeddingRequestResult({}, 0, 0, config.concurrency)

    configured_client = _configured_client(client, config)
    cancelled = Event()
    executor = ThreadPoolExecutor(max_workers=config.concurrency)
    futures = {
        executor.submit(
            _request_batch, configured_client, model, batch, config, cancelled
        ): index
        for index, batch in enumerate(batches)
    }
    completed = {}
    try:
        for future in as_completed(futures):
            try:
                completed[futures[future]] = future.result()
            except CancelledError:
                continue
            except Exception:
                cancelled.set()
                for pending in futures:
                    pending.cancel()
                raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    vectors = {}
    provider_requests = 0
    for index, batch in enumerate(batches):
        encoded, attempts = completed[index]
        vectors.update(dict(zip(batch, encoded, strict=True)))
        provider_requests += attempts
    return EmbeddingRequestResult(
        vectors=vectors,
        provider_requests=provider_requests,
        retries=provider_requests - len(batches),
        concurrency=config.concurrency,
    )


def _without_internal_retries(client):
    with_options = getattr(client, "with_options", None)
    return with_options(max_retries=0) if callable(with_options) else client


def _request_with_retry(client, model, batch):
    client = _without_internal_retries(client)
    for attempt in range(5):
        try:
            return _vectors(
                client.embeddings.create(model=model, input=batch), len(batch)
            )
        except Exception as error:
            if not _transient(error):
                raise
            if attempt == 4:
                raise EmbeddingProviderError(
                    "embedding provider unavailable after retries"
                ) from error
            time.sleep(min(0.25 * 2**attempt, 2.0))


def _cached_embeddings(conn, run_id, hashes):
    if not hashes:
        return []
    return conn.execute(
        """
        SELECT content_hash, sentence, embedding::text
        FROM public.bulk_embedding_cache
        WHERE run_id = %s AND content_hash = ANY(%s)
        """,
        (run_id, hashes),
    ).fetchall()


def embed_missing(
    conn, run_id, client, model, sentences, request_size: int = 100
):
    """Return cached vectors, requesting and storing only missing sentences."""
    if request_size <= 0:
        raise ValueError("request_size must be positive")
    unique = list(dict.fromkeys(sentences))
    sentence_by_hash = {sentence_key(sentence): sentence for sentence in unique}
    if len(sentence_by_hash) != len(unique):
        raise ValueError("embedding cache hash collision")
    cached = {}
    for content_hash, sentence, embedding in _cached_embeddings(
        conn, run_id, list(sentence_by_hash)
    ):
        if sentence_by_hash[content_hash] != sentence:
            raise ValueError("embedding cache hash collision")
        cached[sentence] = embedding

    missing = [
        (content_hash, sentence)
        for content_hash, sentence in sentence_by_hash.items()
        if sentence not in cached
    ]
    for page in batched(missing, request_size):
        texts = [sentence for _, sentence in page]
        vectors = _request_with_retry(client, model, texts)
        rows = [
            (run_id, content_hash, sentence, encoded_vector(vector))
            for (content_hash, sentence), vector in zip(page, vectors, strict=True)
        ]
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO public.bulk_embedding_cache (
                    run_id, content_hash, sentence, embedding
                ) VALUES (%s, %s, %s, %s::halfvec)
                ON CONFLICT (run_id, content_hash) DO UPDATE
                SET sentence = EXCLUDED.sentence
                WHERE public.bulk_embedding_cache.sentence = EXCLUDED.sentence
                """,
                rows,
            )
            if cursor.rowcount != len(rows):
                raise ValueError("embedding cache hash collision")

    result = {}
    for content_hash, sentence, embedding in _cached_embeddings(
        conn, run_id, list(sentence_by_hash)
    ):
        if sentence_by_hash[content_hash] != sentence:
            raise ValueError("embedding cache hash collision")
        result[sentence] = embedding
    if len(result) != len(unique):
        raise RuntimeError("embedding cache write failed")
    return result


def create_embeddings(client, texts, model, *, batch_size=100):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    unique, result = list(dict.fromkeys(texts)), {}
    for start in range(0, len(unique), batch_size):
        batch = unique[start:start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        vectors = _vectors(response, len(batch))
        for vector in vectors:
            _validate_dimensions(vector)
        result.update(dict(zip(batch, vectors, strict=True)))
    return result
