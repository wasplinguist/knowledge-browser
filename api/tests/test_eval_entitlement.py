from uuid import UUID

import pytest

from knowledge_browser.eval_entitlement import allowed_documents, is_visible


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
