from uuid import UUID

import pytest

from knowledge_browser.eval_entitlement import (
    allowed_documents,
    audit_acl,
    entitlement_classes,
    is_visible,
)
from knowledge_browser.profiles import SearchProfile


pytestmark = pytest.mark.unit


USER = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")
THIRD = UUID("00000000-0000-0000-0000-000000000003")
ENG = UUID("10000000-0000-0000-0000-000000000001")
SALES = UUID("10000000-0000-0000-0000-000000000002")


def test_independent_entitlement_defaults_to_deny():
    assert is_visible(None, USER, {ENG}) is False
    assert is_visible({}, USER, {ENG}) is False
    assert is_visible({"visibility": "restricted"}, USER, {ENG}) is False


def test_independent_entitlement_unions_company_user_and_group_access():
    assert is_visible({"visibility": "company"}, OTHER, set()) is True
    assert is_visible({"visibility": "restricted", "users": {USER}}, USER, set()) is True
    assert is_visible({"visibility": "restricted", "groups": {ENG}}, USER, {ENG}) is True
    assert is_visible({"visibility": "restricted", "users": {USER}}, OTHER, {ENG}) is False


def test_independent_entitlement_builds_allowed_document_sets():
    documents = {
        "company": {"visibility": "company"},
        "direct": {"visibility": "restricted", "users": {USER}},
        "group": {"visibility": "restricted", "groups": {ENG}},
        "missing": None,
    }

    assert allowed_documents(documents, USER, {ENG}) == {"company", "direct", "group"}
    assert allowed_documents(documents, OTHER, set()) == {"company"}


def test_entitlement_classes_collapse_users_with_identical_access():
    documents = {
        "company": {"visibility": "company"},
        "eng": {"visibility": "restricted", "groups": {ENG}},
    }
    memberships = {USER: {ENG}, OTHER: {SALES}, THIRD: {ENG, SALES}}

    classes = entitlement_classes(memberships, documents)

    assert classes == {USER: (USER, THIRD), OTHER: (OTHER,)}
    assert sum(len(members) for members in classes.values()) == len(memberships)
    for representative, members in classes.items():
        expected = allowed_documents(documents, representative, memberships[representative])
        for member in members:
            assert allowed_documents(documents, member, memberships[member]) == expected


def test_entitlement_classes_keep_ranking_signals_apart():
    """Personalization reads the user, so equal access is not enough to merge."""
    documents = {"company": {"visibility": "company"}}
    memberships = {USER: set(), OTHER: set(), THIRD: set()}

    merged = entitlement_classes(memberships, documents)
    split = entitlement_classes(
        memberships, documents, {USER: "atlas", OTHER: "atlas", THIRD: "orion"}
    )

    assert merged == {USER: (USER, OTHER, THIRD)}
    assert split == {USER: (USER, OTHER), THIRD: (THIRD,)}


def test_no_unreviewed_ranking_signal_can_read_the_user():
    """Class reduction is exact only while `distinguish` carries every ranking
    input that reads the user. Today that is personalization_weight alone, via
    the searcher's primary_project_id. A new profile field can quietly add a
    second one and turn the entitlement-class audit into an approximation with
    nothing to announce it, so adding one has to fail here first.
    """
    assert set(SearchProfile.__dataclass_fields__) == {
        "name",
        "keyword_limit",
        "semantic_limit",
        "rrf_k",
        "keyword_weight",
        "semantic_weight",
        "freshness_weight",
        "authority_weight",
        "jira_key_weight",
        "personalization_weight",  # the one user-dependent signal
        "query_expansions",
        "embedding_model",
    }


def test_entitlement_classes_separate_direct_grants_from_group_grants():
    documents = {
        "direct": {"visibility": "restricted", "users": {USER}},
        "eng": {"visibility": "restricted", "groups": {ENG}},
        "unreadable": None,
    }
    memberships = {USER: {ENG}, OTHER: {ENG}, THIRD: set()}

    classes = entitlement_classes(memberships, documents)

    assert classes == {USER: (USER,), OTHER: (OTHER,), THIRD: (THIRD,)}


def test_acl_audit_counts_pairs_and_reports_root_and_child_leaks():
    memberships = {USER: {ENG}, OTHER: set()}
    documents = {
        "jira:allowed": {"visibility": "company"},
        "jira:secret": {"visibility": "restricted", "users": {USER}},
    }

    result = audit_acl(
        memberships,
        documents,
        ["query"],
        lambda user, _query: [{
            "source": "jira",
            "external_id": "allowed",
            "matched_external_id": "secret",
        }] if user == OTHER else [],
    )

    assert result == {
        "pairs": 2,
        "hits": 1,
        "restricted_hits": 1,
        "root_leaks": [],
        "child_leaks": [{
            "user_id": str(OTHER),
            "query": "query",
            "document": "jira:secret",
        }],
    }


def test_acl_audit_reports_no_restricted_hits_when_search_retrieves_nothing():
    """Zero leaks is also what an inert search reports, so gates watch this count."""
    documents = {"jira:secret": {"visibility": "restricted", "users": {USER}}}

    result = audit_acl({USER: set()}, documents, ["a", "b"], lambda _user, _query: [])

    assert result["pairs"] == 2
    assert result["root_leaks"] == [] and result["child_leaks"] == []
    assert result["hits"] == 0
    assert result["restricted_hits"] == 0


def test_acl_audit_does_not_count_company_wide_results_as_restricted():
    """A corpus read only through company documents proves nothing about ACL."""
    documents = {"jira:open": {"visibility": "company"}}

    result = audit_acl(
        {USER: set()},
        documents,
        ["query"],
        lambda _user, _query: [{
            "source": "jira", "external_id": "open", "matched_external_id": "open",
        }],
    )

    assert result["hits"] == 1
    assert result["restricted_hits"] == 0
