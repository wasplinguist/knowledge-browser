from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from itertools import batched

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError


class EmbeddingProviderError(RuntimeError):
    safe_code = "embedding_provider_failed"


class EmbeddingResponseError(ValueError):
    safe_code = "embedding_provider_invalid_response"


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
    return isinstance(error, APIStatusError) and error.status_code >= 500


def _request_with_retry(client, model, batch):
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
