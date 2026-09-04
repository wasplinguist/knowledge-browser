import os
from pathlib import Path
from uuid import UUID

import pytest

from conftest import _seed_diverse_acl_shapes
from knowledge_browser.eval_entitlement import (
    allowed_documents,
    entitlement_classes,
    entitlement_snapshot,
)
from knowledge_browser.evaluation import (
    compare_runs,
    evaluate_queries,
    load_golden_queries,
    write_report,
)
from knowledge_browser.profiles import SearchProfile, load_profile
from knowledge_browser.search import hybrid_search, keyword_search, semantic_search


ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "eval" / "fixture_queries.json"
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
    # The golden set sweeps every permission-set signature, so it needs that seed.
    _seed_diverse_acl_shapes(db)
    queries = load_golden_queries(GOLDEN)
    profile = load_profile(RELEASED)

    run = evaluate_queries(
        queries,
        lambda user, query, _profile: hybrid_search(
            db, UUID(user), query, None, profile=profile
        ),
        profile=profile.name,
    )

    assert run["query_count"] == 11
    assert run["scored_query_count"] == 10
    assert run["overall"]["mrr@10"] == pytest.approx(1.0)
    assert run["overall"]["recall@10"] == pytest.approx(1.0)
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
    _seed_diverse_acl_shapes(db)
    memberships, documents = entitlement_snapshot(db)
    terms = [
        "Company", "Direct", "Group", "Missing", "Visible", "Hidden",
        "Shared", "Security", "Pair", "Vacant", "Reused",
    ]
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


@pytest.mark.search_eval
def test_shared_and_layered_grants_reach_exactly_their_members(db):
    """A set naming two groups is a union, and a grant reaching nobody is inert."""
    _seed_diverse_acl_shapes(db)
    memberships, documents = entitlement_snapshot(db)
    both = UUID("00000000-0000-0000-0000-000000000005")
    inert = UUID("00000000-0000-0000-0000-000000000006")
    redundant = UUID("00000000-0000-0000-0000-000000000007")
    reused = UUID("00000000-0000-0000-0000-000000000008")
    group_only = UUID("00000000-0000-0000-0000-000000000003")
    direct_only = UUID("00000000-0000-0000-0000-000000000002")
    company_wide = {"jira:COMPANY-1", "jira:VISIBLE-CHILD", "jira:VISIBLE-ROOT"}

    def visible(user_id):
        return allowed_documents(documents, user_id, memberships[user_id])

    assert "confluence:SHARED-1" in visible(both)
    assert "confluence:SECURITY-1" in visible(both)
    # engineering alone reaches the shared set, but not the security-only set.
    assert "confluence:SHARED-1" in visible(group_only)
    assert "confluence:SECURITY-1" not in visible(group_only)
    # a direct grant reaches the security set without any group membership.
    assert "confluence:SECURITY-1" in visible(direct_only)
    assert "confluence:SHARED-1" not in visible(direct_only)
    # naming a user the granting group already contains grants nothing extra.
    assert "github:PAIR-1" in visible(redundant)
    assert visible(redundant) == visible(group_only)
    # one set governing two documents reaches only the user it names.
    assert visible(reused) == company_wide | {
        "confluence:REUSED-1", "confluence:REUSED-2",
    }
    # a group with a member that grants nothing, and a granted group with no
    # member, both leave access at company-wide.
    assert visible(inert) == company_wide
    assert all("slack:VACANT-1" not in visible(user_id) for user_id in memberships)


@pytest.mark.search_eval
def test_entitlement_classes_collapse_users_that_see_the_same_documents(db):
    """The fixture holds shapes the native corpus cannot express, such as a
    granted group with no members: dataset.py builds groups out of employee
    membership, so a memberless group cannot exist in data/redwood at all."""
    _seed_diverse_acl_shapes(db)
    memberships, documents = entitlement_snapshot(db)

    signatures = {
        (
            permission["visibility"],
            frozenset(permission["users"]),
            frozenset(permission["groups"]),
        )
        for permission in documents.values()
    }
    classes = entitlement_classes(memberships, documents)

    assert len(signatures) == 10
    assert len(classes) == 6
    assert sum(len(members) for members in classes.values()) == len(memberships)
    for representative, members in classes.items():
        expected = allowed_documents(
            documents, representative, memberships[representative]
        )
        for member in members:
            assert allowed_documents(
                documents, member, memberships[member]
            ) == expected


@pytest.mark.search_eval
def test_added_permission_shapes_hold_through_the_semantic_path(db):
    """The added signatures were only ever swept with keyword search.

    Both paths share one ACL predicate, but only the semantic path applies it
    inside the HNSW candidate scan, and the golden sweep passes no query vector
    at all. Give one added document a vector so the ANN scan has to rank it, and
    the layered direct-plus-group grant has to survive that route too.
    """
    _seed_diverse_acl_shapes(db)
    both_groups = UUID("00000000-0000-0000-0000-000000000005")
    group_only = UUID("00000000-0000-0000-0000-000000000003")
    vector = [0.1] * 1536

    def reached(user_id):
        return {
            item["external_id"]
            for item in semantic_search(db, user_id, vector, source="confluence")
        }

    # Reaching SHARED-1 either way is what makes the SECURITY-1 absence ACL
    # rather than an empty result set.
    assert reached(both_groups) >= {"SHARED-1", "SECURITY-1"}
    assert "SHARED-1" in reached(group_only)
    assert "SECURITY-1" not in reached(group_only)
    assert all(
        "VACANT-1" not in {
            item["external_id"]
            for item in semantic_search(db, user_id, vector, source="slack")
        }
        for user_id in entitlement_snapshot(db)[0]
    )


@pytest.mark.search_eval
def test_search_denies_a_user_that_does_not_exist(db):
    """The users EXISTS guard is what stops an unknown id from reading everything."""
    _seed_diverse_acl_shapes(db)
    stranger = UUID("99999999-9999-9999-9999-999999999999")

    for term in ("Company", "Shared", "Security", "Visible", "Pair", "Reused"):
        assert keyword_search(db, stranger, term, limit=100) == []
    assert hybrid_search(db, stranger, "Company", [0.0] * 1536) == []
