import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg
import pytest

from knowledge_browser.evaluation import (
    evaluate_queries,
    load_golden_queries,
    write_report,
)
from knowledge_browser.profiles import load_profile
from knowledge_browser.search import hybrid_search


ROOT = Path(__file__).parents[2]
DATABASE = os.environ.get("NATIVE_EVAL_DATABASE_URL")
QUERIES = Path(os.environ.get("NATIVE_GOLDEN_QUERIES", ROOT / "eval" / "redwood_queries.json"))
PROFILE = Path(os.environ.get(
    "NATIVE_RELEASED_PROFILE", ROOT / "search" / "profiles" / "released.json"
))
MODEL = "text-embedding-3-small"


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\0{text}".encode()).hexdigest()


def _query_embeddings(queries: list[dict]) -> dict[str, list[float]]:
    cache_path = os.environ.get("NATIVE_EMBEDDING_CACHE")
    cache = json.loads(Path(cache_path).read_text()) if cache_path else {}
    vectors = {
        query["query"]: cache[_cache_key(query["query"])]
        for query in queries
        if _cache_key(query["query"]) in cache
    }
    missing = list(dict.fromkeys(
        query["query"] for query in queries if query["query"] not in vectors
    ))
    api_key = os.environ.get("OPENAI_API_KEY")
    if missing and not api_key:
        pytest.fail(
            f"{len(missing)} query embeddings are missing; set OPENAI_API_KEY "
            "or provide NATIVE_EMBEDDING_CACHE"
        )
    for start in range(0, len(missing), 100):
        batch = missing[start:start + 100]
        request = Request(
            "https://api.openai.com/v1/embeddings",
            data=json.dumps({"model": MODEL, "input": batch}).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=120) as response:
            payload = json.load(response)
        by_index = {item["index"]: item["embedding"] for item in payload["data"]}
        vectors.update({text: by_index[index] for index, text in enumerate(batch)})
    return vectors


@pytest.mark.search_eval
@pytest.mark.full_retrieval
@pytest.mark.nightly
def test_native_full_retrieval_quality():
    assert DATABASE, "NATIVE_EVAL_DATABASE_URL is required for full_retrieval"
    queries = load_golden_queries(QUERIES)
    assert len(queries) == 298
    vectors = _query_embeddings(queries)
    profile = load_profile(PROFILE)

    with psycopg.connect(DATABASE) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        users = dict(conn.execute("SELECT email, id FROM users").fetchall())
        run = evaluate_queries(
            queries,
            lambda email, query, _profile: hybrid_search(
                conn, users[email], query, vectors[query], profile=profile
            ),
            profile=profile.name,
        )

    assert run["query_count"] == 298
    assert run["overall"]["forbidden_leaks"] == 0
    report_path = os.environ.get("EVALUATION_REPORT_PATH")
    if report_path:
        write_report(Path(report_path), run)
    print(json.dumps({
        "query_count": run["query_count"],
        "scored_query_count": run["scored_query_count"],
        "overall": run["overall"],
        "latency_ms": run["latency_ms"],
    }, indent=2, sort_keys=True))
    assert run["overall"]["mrr@10"] >= 0.50
    assert run["overall"]["ndcg@10"] >= 0.55
    assert run["overall"]["recall@10"] >= 0.68
