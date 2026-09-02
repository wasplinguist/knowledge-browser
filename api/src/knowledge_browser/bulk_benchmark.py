"""Deterministic throughput gate for the Redwood embedding scheduler."""

from __future__ import annotations

import argparse
import hashlib
from itertools import islice
import json
from pathlib import Path
import resource
import sys
from threading import Lock
import time
import tracemalloc
from types import SimpleNamespace

from .dataset import SOURCES, iter_artifacts, validate_streaming_dataset
from .embedding_index import (
    EmbeddingRequestConfig,
    collect_sentences,
    create_embeddings,
    encoded_vector,
    request_missing_embeddings,
)


MODEL = "text-embedding-3-small"
MEMORY_LIMIT_BYTES = 2 * 1024**3
MINIMUM_THROUGHPUT_RATIO = 5.0


class _FakeEmbeddingClient:
    def __init__(self, delay):
        self.delay = delay
        self.embeddings = self
        self.requests = 0
        self.lock = Lock()

    def with_options(self, **_options):
        return self

    def create(self, *, model, input):
        assert model == MODEL
        time.sleep(self.delay)
        with self.lock:
            self.requests += 1
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    index=index,
                    embedding=[
                        int.from_bytes(
                            hashlib.sha256(text.encode()).digest()[:4],
                            "big",
                        )
                        / 2**32
                    ]
                    * 1536,
                )
                for index, text in enumerate(input)
            ]
        )


def compare_schedulers(sentences, *, provider_delay=0.35):
    unique = list(dict.fromkeys(sentences))

    legacy_client = _FakeEmbeddingClient(provider_delay)
    started = time.perf_counter()
    legacy_raw = create_embeddings(
        legacy_client, unique, MODEL, batch_size=100
    )
    legacy = {
        sentence: encoded_vector(vector)
        for sentence, vector in legacy_raw.items()
    }
    legacy_seconds = time.perf_counter() - started

    new_client = _FakeEmbeddingClient(provider_delay)
    started = time.perf_counter()
    current = request_missing_embeddings(
        new_client, MODEL, unique, EmbeddingRequestConfig()
    )
    new_seconds = time.perf_counter() - started

    return {
        "legacy_provider_requests": legacy_client.requests,
        "legacy_seconds": legacy_seconds,
        "new_provider_requests": new_client.requests,
        "new_seconds": new_seconds,
        "same_vectors": list(legacy.items()) == list(current.vectors.items()),
        "sentences": len(unique),
        "throughput_ratio": legacy_seconds / new_seconds,
    }


def peak_memory_bytes(*, traced_peak, rss_peak, platform=sys.platform):
    normalized_rss = rss_peak if platform == "darwin" else rss_peak * 1024
    return max(int(traced_peak), int(normalized_rss))


def _line_offset(path: Path, line_number: int) -> int:
    if line_number < 1:
        raise ValueError("start line must be positive")
    with path.open("rb") as stream:
        for _ in range(line_number - 1):
            if not stream.readline():
                raise ValueError("start line is outside the source")
        return stream.tell()


def run_benchmark(
    *, data, source="slack", start_line=801, documents=200,
    provider_delay=0.35
):
    if documents < 1:
        raise ValueError("documents must be positive")
    tracemalloc.start()
    try:
        dataset = validate_streaming_dataset(Path(data))
        source_path = dataset.root / "artifacts" / f"{source}.jsonl"
        records = tuple(
            islice(
                iter_artifacts(
                    dataset,
                    source,
                    start_offset=_line_offset(source_path, start_line),
                    start_line=start_line,
                ),
                documents,
            )
        )
        if len(records) != documents:
            raise ValueError("benchmark slice is shorter than requested")
        sentence_values = collect_sentences(
            [record.document for record in records]
        )
        result = compare_schedulers(
            sentence_values, provider_delay=provider_delay
        )
        _, traced_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        **result,
        "documents": documents,
        "peak_memory_bytes": peak_memory_bytes(
            traced_peak=traced_peak, rss_peak=rss_peak
        ),
        "source": source,
        "start_line": start_line,
    }


def _parser():
    parser = argparse.ArgumentParser(
        description="Benchmark Redwood embedding throughput."
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source", choices=SOURCES, default="slack")
    parser.add_argument("--start-line", type=int, default=801)
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--provider-delay", type=float, default=0.35)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    result = run_benchmark(
        data=args.data,
        source=args.source,
        start_line=args.start_line,
        documents=args.documents,
        provider_delay=args.provider_delay,
    )
    print(json.dumps(result, sort_keys=True))
    passed = (
        result["same_vectors"]
        and result["throughput_ratio"] >= MINIMUM_THROUGHPUT_RATIO
        and result["peak_memory_bytes"] < MEMORY_LIMIT_BYTES
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
