from uuid import UUID

import pytest

import knowledge_browser.search as search_module
from conftest import _seed_malformed_root_chain
from knowledge_browser.profiles import SearchProfile
from knowledge_browser.search import (
    hybrid_search,
    keyword_search,
    read_chunk,
    semantic_search,
)


def _hit(external_id, root_id, *, child=False, chunk_id=None):
    return {
        "chunk_id": chunk_id or external_id,
        "field": "body",
        "matched_field": "body",
        "excerpt": external_id,
        "root_id": root_id,
        "external_id": external_id,
        "title": external_id,
        "source": "jira",
        "author": "Ada",
        "matched_author": "Ada",
        "container": "Atlas",
        "created_at": None,
        "updated_at": None,
        "url": None,
        "is_child": child,
        "chunk_index": 0,
        "matched_external_id": external_id,
        "matched_created_at": None,
        "matched_updated_at": None,
    }


@pytest.mark.unit
def test_hybrid_search_uses_rrf_and_returns_one_result_per_root(monkeypatch):
    root_a = UUID("30000000-0000-0000-0000-000000000001")
    root_b = UUID("30000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(
        search_module,
        "keyword_search",
        lambda *_args, **_kwargs: [_hit("A", root_a), _hit("B", root_b)],
    )
    monkeypatch.setattr(
        search_module,
        "semantic_search",
        lambda *_args, **_kwargs: [_hit("A-child", root_a, child=True), _hit("B", root_b)],
    )

    items = hybrid_search(None, "user", "query", [1.0])

    assert [item["external_id"] for item in items] == ["A-child", "B"]
    assert len(items) == 2
    assert all("root_id" not in item and "is_child" not in item for item in items)
    assert items[0]["score"] == pytest.approx(2 / 61)


@pytest.mark.unit
def test_hybrid_search_has_deterministic_ties(monkeypatch):
    root_a = UUID("30000000-0000-0000-0000-000000000001")
    root_b = UUID("30000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(
        search_module,
        "keyword_search",
        lambda *_args, **_kwargs: [
            _hit("B", root_b),
            _hit("A", root_a),
        ],
    )
    monkeypatch.setattr(
        search_module,
        "semantic_search",
        lambda *_args, **_kwargs: [_hit("A", root_a), _hit("B", root_b)],
    )

    items = hybrid_search(
        None,
        "user",
        "query",
        [1.0],
    )

    assert items[0]["score"] == pytest.approx(items[1]["score"])
    assert [item["external_id"] for item in items] == ["A", "B"]


@pytest.mark.integration
def test_keyword_search_filters_acl_before_returning_content(db):
    company_user = UUID("00000000-0000-0000-0000-000000000001")
    group_user = UUID("00000000-0000-0000-0000-000000000003")
    other_user = UUID("00000000-0000-0000-0000-000000000004")

    assert keyword_search(db, company_user, "Company")[0]["external_id"] == "COMPANY-1"
    assert keyword_search(db, group_user, "Group")[0]["external_id"] == "GROUP-1"
    assert keyword_search(db, other_user, "Group") == []
    assert keyword_search(db, UUID(int=0), "Company") == []


@pytest.mark.integration
def test_keyword_search_enforces_child_and_root_acl_in_both_directions(db):
    company_user = UUID("00000000-0000-0000-0000-000000000001")

    assert keyword_search(db, company_user, "Visible child") == []
    assert keyword_search(db, company_user, "Hidden child") == []


@pytest.mark.integration
def test_semantic_search_filters_acl_and_ranks_each_chunk_once(db):
    group_user = UUID("00000000-0000-0000-0000-000000000003")
    other_user = UUID("00000000-0000-0000-0000-000000000004")
    vector = [1.0, *([0.0] * 1535)]
    db.execute(
        "UPDATE sentences SET embedding = %s::halfvec "
        "WHERE source = 'slack' AND chunk_id = 'slack:GROUP-1:0'",
        ("[" + ",".join(map(str, vector)) + "]",),
    )
    db.execute(
        "INSERT INTO sentences (source, chunk_id, sentence_index, sentence, "
        "embedding, embedding_model) VALUES "
        "('slack', 'slack:GROUP-1:0', 1, 'Second group sentence', "
        "%s::halfvec, 'test-embedding')",
        ("[" + ",".join(map(str, vector)) + "]",),
    )

    allowed = semantic_search(db, group_user, vector, source="slack")
    denied = semantic_search(db, other_user, vector, source="slack")

    assert allowed[0]["external_id"] == "GROUP-1"
    assert len(allowed) == len({item["chunk_id"] for item in allowed})
    assert denied == []


@pytest.mark.integration
def test_semantic_search_enforces_child_and_root_acl_in_both_directions(db):
    company_user = UUID("00000000-0000-0000-0000-000000000001")
    vector = [1.0, *([0.0] * 1535)]
    encoded = "[" + ",".join(map(str, vector)) + "]"
    db.execute(
        "UPDATE sentences SET embedding = %s::halfvec WHERE chunk_id IN "
        "('jira:VISIBLE-CHILD:0', 'jira:HIDDEN-CHILD:0')",
        (encoded,),
    )

    items = semantic_search(db, company_user, vector, source="jira")

    assert {item["matched_external_id"] for item in items}.isdisjoint(
        {"VISIBLE-CHILD", "HIDDEN-CHILD"}
    )


@pytest.mark.integration
def test_retrieval_rejects_a_non_canonical_root_chain(db):
    _seed_malformed_root_chain(db)
    user = UUID("00000000-0000-0000-0000-000000000001")
    vector = [1.0, *([0.0] * 1535)]
    db.execute(
        "UPDATE sentences SET embedding = %s::halfvec "
        "WHERE chunk_id = 'jira:CHAIN-CHILD:0'",
        ("[" + ",".join(map(str, vector)) + "]",),
    )

    assert keyword_search(db, user, "Chain child") == []
    semantic_items = semantic_search(db, user, vector, source="jira")
    assert "CHAIN-CHILD" not in {
        item["matched_external_id"] for item in semantic_items
    }


@pytest.mark.integration
def test_read_chunk_returns_full_text_only_when_child_and_root_are_allowed(db):
    company_user = UUID("00000000-0000-0000-0000-000000000001")

    allowed = read_chunk(db, company_user, "jira", "jira:COMPANY-1:0")
    hidden_root = read_chunk(db, company_user, "jira", "jira:VISIBLE-CHILD:0")
    hidden_child = read_chunk(db, company_user, "jira", "jira:HIDDEN-CHILD:0")

    assert allowed["text"] == "Company body"
    assert allowed["external_id"] == "COMPANY-1"
    assert hidden_root is None
    assert hidden_child is None
