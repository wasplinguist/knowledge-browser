import json
import os
from pathlib import Path

import psycopg
import pytest

from knowledge_browser.eval_entitlement import audit_acl, entitlement_snapshot
from knowledge_browser.profiles import load_profile
from knowledge_browser.search import hybrid_search


NATIVE_DATABASE = os.environ.get("NATIVE_EVAL_DATABASE_URL")
ROOT = Path(__file__).parents[2]
NATIVE_QUERIES = Path(os.environ.get(
    "NATIVE_GOLDEN_QUERIES", ROOT / "eval" / "native_acl_queries.json"
))
NATIVE_PROFILE = Path(os.environ.get(
    "NATIVE_RELEASED_PROFILE", ROOT / "search" / "profiles" / "released.json"
))


@pytest.mark.search_eval
@pytest.mark.full_acl
@pytest.mark.nightly
@pytest.mark.skipif(
    not NATIVE_DATABASE,
    reason="NATIVE_EVAL_DATABASE_URL is not configured",
)
def test_native_corpus_has_zero_root_and_child_acl_leaks():
    """Run only at a configured manual/nightly release gate."""
    queries = json.loads(NATIVE_QUERIES.read_text(encoding="utf-8"))
    profile = load_profile(NATIVE_PROFILE)
    with psycopg.connect(NATIVE_DATABASE) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        memberships, documents = entitlement_snapshot(conn)
        result = audit_acl(
            memberships,
            documents,
            [item["query"] for item in queries],
            lambda user, query: hybrid_search(
                conn, user, query, [0.0] * 1536, profile=profile
            ),
        )

    assert result["pairs"] == len(memberships) * len(queries)
    assert result["root_leaks"] == []
    assert result["child_leaks"] == []
