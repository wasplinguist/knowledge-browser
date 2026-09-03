import json
import os
from pathlib import Path

import psycopg
import pytest

from knowledge_browser.eval_query_embeddings import load_query_embeddings
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
EMBEDDINGS = Path(os.environ.get(
    "NATIVE_QUERY_EMBEDDINGS",
    ROOT / "eval" / ".cache" / "redwood_query_embeddings.json",
))


def _query_embeddings(queries: list[dict]) -> dict[str, list[float]]:
    return load_query_embeddings(
        EMBEDDINGS,
        queries,
        allow_requests=bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    )


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

    report_path = os.environ.get("EVALUATION_REPORT_PATH")
    if report_path:
        write_report(Path(report_path), run)
    print(json.dumps({
        "query_count": run["query_count"],
        "scored_query_count": run["scored_query_count"],
        "families": run["families"],
        "overall": run["overall"],
        "latency_ms": run["latency_ms"],
    }, indent=2, sort_keys=True))
    assert run["query_count"] == 298
    assert run["overall"]["forbidden_leaks"] == 0
    assert run["overall"]["mrr@10"] >= 0.50
    assert run["overall"]["ndcg@10"] >= 0.55
    assert run["overall"]["recall@10"] >= 0.68
