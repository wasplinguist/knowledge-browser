import json
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


def _hit(
    external_id,
    root_id,
    *,
    child=False,
    chunk_id=None,
    source="jira",
    field="body",
    excerpt=None,
    updated_at=None,
    matched_updated_at=None,
):
    return {
        "chunk_id": chunk_id or external_id,
        "field": field,
        "matched_field": field,
        "excerpt": excerpt or external_id,
        "root_id": root_id,
        "external_id": external_id,
        "title": external_id,
        "source": source,
        "author": "Ada",
        "matched_author": "Ada",
        "container": "Atlas",
        "created_at": None,
        "updated_at": updated_at,
        "url": None,
        "is_child": child,
        "chunk_index": 0,
        "matched_external_id": external_id,
        "matched_created_at": None,
        "matched_updated_at": matched_updated_at,
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


@pytest.mark.unit
@pytest.mark.parametrize("signal", ["latest", "newest", "most recent", "current"])
def test_freshness_signals_prefer_the_newest_visible_evidence(monkeypatch, signal):
    stale = _hit(
        "stale", "stale", source="confluence",
        updated_at="2026-01-01T00:00:00Z",
    )
    fresh_child = _hit(
        "fresh", "fresh", updated_at="2025-12-01T00:00:00Z",
        matched_updated_at="2026-03-01T00:00:00Z",
    )
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: [stale, fresh_child])

    items = hybrid_search(
        None,
        "user",
        f"{signal} project update",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, freshness_weight=0.05
        ),
    )

    assert [item["external_id"] for item in items] == ["fresh", "stale"]


@pytest.mark.unit
def test_historical_query_keeps_relevance_order(monkeypatch):
    old = _hit("old", "old", updated_at="2025-01-01T00:00:00Z")
    new = _hit("new", "new", updated_at="2026-01-01T00:00:00Z")
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: [old, new])

    items = hybrid_search(
        None,
        "user",
        "Why did the original approach fail?",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, freshness_weight=0.05
        ),
    )

    assert [item["external_id"] for item in items] == ["old", "new"]


@pytest.mark.unit
def test_source_authority_prefers_jira_for_assignee_question(monkeypatch):
    plan = _hit("plan", "plan", source="confluence")
    issue = _hit("issue", "issue", source="jira")
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: [plan, issue])

    items = hybrid_search(
        None,
        "user",
        "Who is the assignee?",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, authority_weight=0.05
        ),
    )

    assert [item["external_id"] for item in items] == ["issue", "plan"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What did Slack say about the ticket status?", "slack"),
        ("What is the PR status?", "github"),
        ("What is the policy status?", "confluence"),
    ],
)
def test_authority_uses_the_clearly_named_source_for_mixed_signals(
    monkeypatch, query, expected
):
    hits = [
        _hit("jira", "jira", source="jira"),
        _hit("github", "github", source="github"),
        _hit("confluence", "confluence", source="confluence"),
        _hit("slack", "slack", source="slack"),
    ]
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: hits)

    items = hybrid_search(
        None,
        "user",
        query,
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, authority_weight=0.05
        ),
    )

    assert items[0]["source"] == expected


@pytest.mark.unit
def test_freshness_keeps_the_newest_timestamp_from_every_match_for_a_root(
    monkeypatch,
):
    root_a = UUID("30000000-0000-0000-0000-000000000001")
    root_b = UUID("30000000-0000-0000-0000-000000000002")
    hits = [
        _hit("A", root_a, child=True, matched_updated_at="2026-01-01T00:00:00Z"),
        _hit("B", root_b, updated_at="2026-02-01T00:00:00Z"),
        _hit("A", root_a, child=True, matched_updated_at="2026-03-01T00:00:00Z"),
    ]
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: hits)
    baseline = SearchProfile(name="baseline", semantic_weight=0)
    candidate = SearchProfile(
        name="candidate", semantic_weight=0, freshness_weight=0.05
    )

    before = {
        item["external_id"]: item["score"]
        for item in hybrid_search(None, "user", "latest update", None, profile=baseline)
    }
    after = {
        item["external_id"]: item["score"]
        for item in hybrid_search(None, "user", "latest update", None, profile=candidate)
    }

    assert after["A"] - before["A"] == pytest.approx(0.05 / 61)
    assert after["B"] == pytest.approx(before["B"])


@pytest.mark.unit
def test_exact_jira_key_survives_child_snippet_selection(monkeypatch):
    github_root = UUID("30000000-0000-0000-0000-000000000001")
    jira_root = UUID("30000000-0000-0000-0000-000000000002")
    github = _hit(
        "github-mention", github_root, source="github", excerpt="NIMREL-401"
    )
    exact = _hit(
        "exact-jira", jira_root, field="issue_metadata", excerpt="NIMREL-401"
    )
    child = _hit(
        "exact-jira", jira_root, child=True, field="comment",
        excerpt="Investigation update",
    )
    monkeypatch.setattr(
        search_module, "keyword_search", lambda *_args: [github, exact]
    )
    monkeypatch.setattr(
        search_module, "semantic_search", lambda *_args: [github, child]
    )

    items = hybrid_search(
        None,
        "user",
        "NIMREL-401",
        [1.0],
        profile=SearchProfile(name="candidate", jira_key_weight=1.0),
    )

    assert items[0]["external_id"] == "exact-jira"
    assert items[0]["matched_field"] == "comment"


@pytest.mark.unit
def test_exact_jira_key_beats_mentions_and_partial_keys(monkeypatch):
    mention = _hit(
        "github-mention", "github-mention", source="github",
        excerpt="Resolves NIMREL-401",
    )
    partial = _hit(
        "partial", "partial", field="issue_metadata", excerpt="NIMREL-4010"
    )
    exact = _hit(
        "exact", "exact", field="issue_metadata", excerpt="NIMREL-401 Resolved"
    )
    monkeypatch.setattr(
        search_module, "keyword_search", lambda *_args: [mention, partial, exact]
    )

    items = hybrid_search(
        None,
        "user",
        "NIMREL-401",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, jira_key_weight=1.0
        ),
    )

    assert [item["external_id"] for item in items] == [
        "exact", "github-mention", "partial"
    ]


class _ProjectConnection:
    def execute(self, _query, _parameters):
        return self

    def fetchall(self):
        return [("mine",)]


@pytest.mark.unit
def test_personalization_reranks_only_retrieved_primary_project_results(monkeypatch):
    other = _hit("other", "other")
    mine = _hit("mine", "mine")
    monkeypatch.setattr(search_module, "keyword_search", lambda *_args: [other, mine])

    items = hybrid_search(
        _ProjectConnection(),
        "user",
        "incident relevant to my work",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, personalization_weight=0.05
        ),
    )

    assert [item["external_id"] for item in items] == ["mine", "other"]
    assert {item["external_id"] for item in items} == {"mine", "other"}


@pytest.mark.integration
def test_personalization_reads_existing_indexed_project_metadata(db, monkeypatch):
    user = UUID("00000000-0000-0000-0000-000000000001")
    roots = dict(db.execute(
        "SELECT external_id, id FROM documents "
        "WHERE external_id IN ('COMPANY-1', 'VISIBLE-ROOT')"
    ).fetchall())
    db.execute(
        "UPDATE users SET raw_payload = %s WHERE id = %s",
        (json.dumps({"primary_project_id": "project-mine"}), user),
    )
    db.execute(
        "UPDATE documents SET raw_payload = %s WHERE external_id = 'COMPANY-1'",
        (json.dumps({"project_ids": ["project-mine"]}),),
    )
    monkeypatch.setattr(
        search_module,
        "keyword_search",
        lambda *_args: [
            _hit("VISIBLE-ROOT", roots["VISIBLE-ROOT"]),
            _hit("COMPANY-1", roots["COMPANY-1"]),
        ],
    )

    items = hybrid_search(
        db,
        user,
        "incident relevant to my work",
        None,
        profile=SearchProfile(
            name="candidate", semantic_weight=0, personalization_weight=0.05
        ),
    )

    assert [item["external_id"] for item in items] == ["COMPANY-1", "VISIBLE-ROOT"]


@pytest.mark.search_eval
def test_focused_enterprise_comparison_has_wins_and_no_protected_losses(monkeypatch):
    cases = {
        "latest project update": [
            _hit("stale", "stale", updated_at="2026-01-01T00:00:00Z"),
            _hit("fresh", "fresh", updated_at="2026-03-01T00:00:00Z"),
        ],
        "Who is the assignee?": [
            _hit("plan", "plan", source="confluence"),
            _hit("jira", "jira", source="jira"),
        ],
        "NIMREL-401": [
            _hit("mention", "mention", source="github", excerpt="NIMREL-401"),
            _hit("exact", "exact", field="issue_metadata", excerpt="NIMREL-401"),
        ],
        "incident relevant to my work": [_hit("other", "other"), _hit("mine", "mine")],
        "Why did the original approach fail?": [
            _hit("historical", "historical", source="confluence", updated_at="2025-01-01T00:00:00Z"),
            _hit("newer", "newer", source="jira", updated_at="2026-01-01T00:00:00Z"),
        ],
    }
    expected = {
        "latest project update": "fresh",
        "Who is the assignee?": "jira",
        "NIMREL-401": "exact",
        "incident relevant to my work": "mine",
        "Why did the original approach fail?": "historical",
    }
    current_query = ""
    monkeypatch.setattr(
        search_module, "keyword_search", lambda *_args: cases[current_query]
    )
    baseline = SearchProfile(name="baseline", semantic_weight=0)
    candidate = SearchProfile(
        name="candidate",
        semantic_weight=0,
        freshness_weight=0.05,
        authority_weight=0.05,
        jira_key_weight=1.0,
        personalization_weight=0.05,
    )
    wins = losses = 0
    for query, wanted in expected.items():
        current_query = query
        before = hybrid_search(_ProjectConnection(), "user", query, None, profile=baseline)[0]["external_id"]
        after = hybrid_search(_ProjectConnection(), "user", query, None, profile=candidate)[0]["external_id"]
        wins += before != wanted and after == wanted
        losses += before == wanted and after != wanted

    assert {"wins": wins, "losses": losses} == {"wins": 4, "losses": 0}


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


@pytest.mark.integration
def test_read_chunk_context_returns_same_field_siblings_with_selected_first(db):
    company_user = UUID("00000000-0000-0000-0000-000000000001")
    document_id = UUID("30000000-0000-0000-0000-000000000001")
    db.execute(
        """
        INSERT INTO chunks (
          source, id, document_id, field, text, chunk_index, content_hash
        ) VALUES
          ('jira', 'jira:COMPANY-1:1', %s, 'body', 'Root cause', 1, 'context-1'),
          ('jira', 'jira:COMPANY-1:2', %s, 'body', 'Resolution', 2, 'context-2'),
          ('jira', 'jira:COMPANY-1:title', %s, 'title', 'Title', 3, 'context-3')
        """,
        (document_id, document_id, document_id),
    )

    context = search_module.read_chunk_context(
        db, company_user, "jira", "jira:COMPANY-1:1", limit=3
    )

    assert [item["chunk_id"] for item in context] == [
        "jira:COMPANY-1:1",
        "jira:COMPANY-1:0",
        "jira:COMPANY-1:2",
    ]
    assert search_module.read_chunk_context(
        db, company_user, "jira", "jira:VISIBLE-CHILD:0", limit=3
    ) == []
