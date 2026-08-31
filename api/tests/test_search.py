from uuid import UUID

import pytest

import knowledge_browser.search as search_module
from conftest import _seed_malformed_root_chain
from knowledge_browser.profiles import SearchProfile
from knowledge_browser.search import hybrid_search, keyword_search, semantic_search


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
    monkeypatch.setattr(
        search_module,
        "keyword_search",
        lambda *_args, **_kwargs: [
            _hit("B", UUID("30000000-0000-0000-0000-000000000002")),
            _hit("A", UUID("30000000-0000-0000-0000-000000000001")),
        ],
    )
    monkeypatch.setattr(search_module, "semantic_search", lambda *_args, **_kwargs: [])

    items = hybrid_search(
        None,
        "user",
        "query",
        None,
        profile=SearchProfile(name="keyword", semantic_weight=0),
    )

    assert [item["external_id"] for item in items] == ["B", "A"]


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
def test_semantic_search_filters_acl_and_ranks_each_chunk_once(db):
    group_user = UUID("00000000-0000-0000-0000-000000000003")
    other_user = UUID("00000000-0000-0000-0000-000000000004")
    vector = [1.0, *([0.0] * 1535)]
    db.execute(
        "UPDATE sentences SET embedding = %s::halfvec "
        "WHERE source = 'slack' AND chunk_id = 'slack:GROUP-1:0'",
        ("[" + ",".join(map(str, vector)) + "]",),
    )

    allowed = semantic_search(db, group_user, vector, source="slack")
    denied = semantic_search(db, other_user, vector, source="slack")

    assert allowed[0]["external_id"] == "GROUP-1"
    assert len(allowed) == len({item["chunk_id"] for item in allowed})
    assert denied == []


@pytest.mark.integration
def test_retrieval_rejects_a_non_canonical_root_chain(db):
    _seed_malformed_root_chain(db)
    user = UUID("00000000-0000-0000-0000-000000000001")

    assert keyword_search(db, user, "Chain child") == []
