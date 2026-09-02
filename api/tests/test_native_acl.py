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
    "NATIVE_GOLDEN_QUERIES", ROOT / "eval" / "redwood_queries.json"
))
NATIVE_PROFILE = Path(os.environ.get(
    "NATIVE_RELEASED_PROFILE", ROOT / "search" / "profiles" / "released.json"
))


@pytest.mark.search_eval
@pytest.mark.full_acl
@pytest.mark.nightly
def test_native_corpus_has_zero_root_and_child_acl_leaks():
    """Run only at a configured manual/nightly release gate."""
    assert NATIVE_DATABASE, "NATIVE_EVAL_DATABASE_URL is required for full_acl"
    queries = json.loads(NATIVE_QUERIES.read_text(encoding="utf-8"))
    assert len(queries) == 298
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
                conn, user, query, [0.0] * 1536, profile=profile
            ),
        )

    assert len(memberships) == 7_245
    assert root_count == 13_214
    assert result["pairs"] == 2_159_010
    assert result["root_leaks"] == []
    assert result["child_leaks"] == []
