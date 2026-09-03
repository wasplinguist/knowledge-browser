"""Cached query embeddings so evaluation runs exercise the real vector channel.

The exhaustive ACL and retrieval gates used to pass a zero vector, which made
the semantic half of hybrid search inert: the audited result sets never
contained the documents a leak would come from. Embedding the golden queries
once and caching them keeps those gates cheap without making them degenerate.
"""

from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
BATCH_SIZE = 128


class QueryEmbeddingError(RuntimeError):
    """The cache cannot be completed without contacting the provider."""


def query_key(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _default_embed(texts: Sequence[str], model: str) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = list(texts[start:start + BATCH_SIZE])
        response = client.embeddings.create(model=model, input=batch)
        vectors.extend(item.embedding for item in response.data)
    return vectors


def read_cache(path: Path, model: str = MODEL) -> dict[str, list[float]]:
    """Return cached vectors keyed by query text hash, or {} when unusable."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("model") != model or payload.get("dimensions") != DIMENSIONS:
        return {}
    return {
        key: vector
        for key, vector in payload.get("vectors", {}).items()
        if isinstance(vector, list) and len(vector) == DIMENSIONS
    }


def load_query_embeddings(
    path: Path,
    queries: Sequence[Mapping[str, Any]],
    embed: Callable[[Sequence[str], str], list[list[float]]] | None = None,
    model: str = MODEL,
    *,
    allow_requests: bool = True,
) -> dict[str, list[float]]:
    """Map query text to its vector, requesting only what the cache is missing.

    Vectors are keyed by a hash of the query text, so editing a golden query
    invalidates exactly that entry instead of silently reusing a stale vector.
    """
    texts = list(dict.fromkeys(str(query["query"]) for query in queries))
    cached = read_cache(path, model)
    missing = [text for text in texts if query_key(text) not in cached]

    if missing:
        if not allow_requests:
            raise QueryEmbeddingError(
                f"{len(missing)} golden queries are missing from {path}"
            )
        vectors = (embed or _default_embed)(missing, model)
        if len(vectors) != len(missing):
            raise QueryEmbeddingError("embedding provider returned the wrong count")
        for text, vector in zip(missing, vectors, strict=True):
            if len(vector) != DIMENSIONS:
                raise QueryEmbeddingError("embedding provider returned wrong dimensions")
            cached[query_key(text)] = list(vector)
        write_cache(path, cached, model)

    return {text: cached[query_key(text)] for text in texts}


def write_cache(path: Path, vectors: Mapping[str, list[float]], model: str = MODEL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"model": model, "dimensions": DIMENSIONS, "vectors": dict(vectors)},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    arguments = parser.parse_args(argv)

    queries = json.loads(arguments.queries.read_text(encoding="utf-8"))
    before = len(read_cache(arguments.out, arguments.model))
    vectors = load_query_embeddings(arguments.out, queries, model=arguments.model)
    after = len(read_cache(arguments.out, arguments.model))
    print(
        f"queries={len(queries)} distinct={len(vectors)} "
        f"cached_before={before} cached_after={after} path={arguments.out}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
