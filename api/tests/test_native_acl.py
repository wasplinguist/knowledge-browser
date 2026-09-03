import json
import os
from pathlib import Path

import psycopg
import pytest

from knowledge_browser.eval_entitlement import (
    audit_acl,
    entitlement_classes,
    entitlement_snapshot,
)
from knowledge_browser.eval_query_embeddings import (
    QueryEmbeddingError,
    load_query_embeddings,
)
from knowledge_browser.profiles import load_profile
from knowledge_browser.search import hybrid_search


NATIVE_DATABASE = os.environ.get("NATIVE_EVAL_DATABASE_URL")
ROOT = Path(__file__).parents[2]
NATIVE_QUERIES = Path(os.environ.get(
    "NATIVE_GOLDEN_QUERIES", ROOT / "eval" / "redwood_queries.json"
))
NATIVE_PROFILE = Path(os.environ.get(
    "NATIVE_RELEASED_PROFILE", ROOT / "search" / "profiles" / "released.json"
))
NATIVE_EMBEDDINGS = Path(os.environ.get(
    "NATIVE_QUERY_EMBEDDINGS",
    ROOT / "eval" / ".cache" / "redwood_query_embeddings.json",
))


def _golden_queries():
    queries = json.loads(NATIVE_QUERIES.read_text(encoding="utf-8"))
    assert len(queries) == 298
    return queries


def _query_embeddings(queries):
    """Real vectors or nothing: a zero vector would silently disable ANN recall."""
    try:
        return load_query_embeddings(
            NATIVE_EMBEDDINGS,
            queries,
            allow_requests=bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        )
    except QueryEmbeddingError as error:
        pytest.skip(
            f"golden query embeddings unavailable ({error}); build them with "
            "python -m knowledge_browser.eval_query_embeddings "
            f"--queries {NATIVE_QUERIES} --out {NATIVE_EMBEDDINGS}"
        )


@pytest.mark.search_eval
@pytest.mark.nightly
def test_native_corpus_entitlement_classes_have_no_acl_leaks():
    """Every user, deduplicated: one representative per distinct access set."""
    if not NATIVE_DATABASE:
        pytest.skip("NATIVE_EVAL_DATABASE_URL is required for the native ACL audit")
    queries = _golden_queries()
    embeddings = _query_embeddings(queries)
    profile = load_profile(NATIVE_PROFILE)

    with psycopg.connect(NATIVE_DATABASE) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        memberships, documents = entitlement_snapshot(conn)
        # released.json carries personalization_weight, and that boost reads the
        # user's own project, so users differing there cannot share a class.
        projects = dict(conn.execute(
            """
            SELECT id, COALESCE(
                     raw_payload->'raw_payload'->>'primary_project_id',
                     raw_payload->>'primary_project_id'
                   )
            FROM users
            """
        ).fetchall())
        classes = entitlement_classes(memberships, documents, projects)
        sampled = {user_id: memberships[user_id] for user_id in classes}
        result = audit_acl(
            sampled,
            documents,
            [item["query"] for item in queries],
            lambda user, query: hybrid_search(
                conn, user, query, embeddings[query], profile=profile
            ),
        )

    covered = sum(len(members) for members in classes.values())
    assert len(memberships) == 7_245
    assert covered == len(memberships)
    assert len(classes) == 4
    assert result["pairs"] == len(classes) * len(queries)
    assert result["root_leaks"] == []
    assert result["child_leaks"] == []


@pytest.mark.search_eval
@pytest.mark.full_acl
@pytest.mark.nightly
def test_native_corpus_has_zero_root_and_child_acl_leaks():
    """Run only at a configured manual release gate."""
    assert NATIVE_DATABASE, "NATIVE_EVAL_DATABASE_URL is required for full_acl"
    queries = _golden_queries()
    embeddings = _query_embeddings(queries)
    profile = load_profile(NATIVE_PROFILE)
    with psycopg.connect(NATIVE_DATABASE) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        memberships, documents = entitlement_snapshot(conn)
        root_count = conn.execute(
            "SELECT count(*) FROM documents WHERE id = root_document_id"
        ).fetchone()[0]
        result = audit_acl(
            memberships,
            documents,
            [item["query"] for item in queries],
            lambda user, query: hybrid_search(
                conn, user, query, embeddings[query], profile=profile
            ),
        )

    assert len(memberships) == 7_245
    assert root_count == 13_214
    assert result["pairs"] == 2_159_010
    assert result["root_leaks"] == []
    assert result["child_leaks"] == []
