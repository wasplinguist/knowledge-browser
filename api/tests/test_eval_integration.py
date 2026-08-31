import os
from pathlib import Path
from uuid import UUID

import pytest

from knowledge_browser.eval_entitlement import allowed_documents, entitlement_snapshot
from knowledge_browser.evaluation import (
    compare_runs,
    evaluate_queries,
    load_golden_queries,
    write_report,
)
from knowledge_browser.profiles import SearchProfile, load_profile
from knowledge_browser.search import hybrid_search, keyword_search


ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "eval" / "golden_queries.json"
RELEASED = ROOT / "search" / "profiles" / "released.json"


@pytest.mark.search_eval
def test_independent_snapshot_matches_small_fixture_permissions(db):
    memberships, documents = entitlement_snapshot(db)
    user = UUID("00000000-0000-0000-0000-000000000001")

    visible = allowed_documents(documents, user, memberships[user])

    assert "jira:COMPANY-1" in visible
    assert "confluence:DIRECT-1" not in visible


@pytest.mark.search_eval
def test_committed_golden_queries_have_no_forbidden_search_leaks(db):
    queries = load_golden_queries(GOLDEN)
    profile = load_profile(RELEASED)

    run = evaluate_queries(
        queries,
        lambda user, query, _profile: hybrid_search(
            db, UUID(user), query, None, profile=profile
        ),
        profile=profile.name,
    )

    assert run["query_count"] == 4
    assert run["overall"]["mrr@10"] == pytest.approx(0.75)
    assert run["overall"]["recall@10"] == pytest.approx(0.75)
    assert run["overall"]["forbidden_leaks"] == 0

    report_path = os.environ.get("EVALUATION_REPORT_PATH")
    if report_path:
        baseline = SearchProfile(name="baseline")
        baseline_run = evaluate_queries(
            queries,
            lambda user, query, _profile: hybrid_search(
                db, UUID(user), query, None, profile=baseline
            ),
            profile=baseline.name,
        )
        write_report(Path(report_path), {
            "released": run,
            "comparison": compare_runs(baseline_run, run),
        })


@pytest.mark.search_eval
def test_configured_corpus_has_zero_root_and_child_acl_leaks(db):
    """Fast fixture smoke; the native matrix is a separate full_acl test."""
    memberships, documents = entitlement_snapshot(db)
    terms = ["Company", "Direct", "Group", "Missing", "Visible", "Hidden"]
    leaks = []

    for user_id, groups in memberships.items():
        expected = allowed_documents(documents, user_id, groups)
        for term in terms:
            for item in keyword_search(db, user_id, term, limit=100):
                root = f'{item["source"]}:{item["external_id"]}'
                child = f'{item["source"]}:{item["matched_external_id"]}'
                if root not in expected or child not in expected:
                    leaks.append((str(user_id), term, root, child))

    assert leaks == []
