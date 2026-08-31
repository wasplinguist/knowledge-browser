from uuid import UUID

import pytest

from knowledge_browser.eval_entitlement import audit_acl, allowed_documents, is_visible


pytestmark = pytest.mark.unit


USER = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")
ENG = UUID("10000000-0000-0000-0000-000000000001")


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
        "root_leaks": [],
        "child_leaks": [{
            "user_id": str(OTHER),
            "query": "query",
            "document": "jira:secret",
        }],
    }
