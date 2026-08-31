import json
from types import SimpleNamespace

import pytest

import knowledge_browser.answer as answer_module
from knowledge_browser.evaluation import evaluate_grounding


pytestmark = pytest.mark.rag_eval


def test_grounded_answer_eval_accepts_only_opened_acl_safe_evidence():
    answer = {
        "evidence_status": "complete",
        "citations": [
            {"source": "jira", "chunk_id": "jira:COMPANY-1:0"},
            {"source": "slack", "chunk_id": "slack:GROUP-1:0"},
        ],
    }
    opened = {
        ("jira", "jira:COMPANY-1:0"),
        ("slack", "slack:GROUP-1:0"),
    }

    assert evaluate_grounding(answer, opened)["grounded"] is True


def test_grounding_eval_rejects_duplicates_unopened_and_empty_complete_answers():
    answer = {
        "evidence_status": "complete",
        "citations": [
            {"source": "jira", "chunk_id": "one"},
            {"source": "jira", "chunk_id": "one"},
            {"source": "slack", "chunk_id": "not-opened"},
        ],
    }

    result = evaluate_grounding(answer, {("jira", "one")})

    assert result == {
        "grounded": False,
        "duplicate_citations": 1,
        "unopened_citations": [("slack", "not-opened")],
    }
    assert evaluate_grounding(
        {"evidence_status": "complete", "citations": []}, set()
    )["grounded"] is False
    assert evaluate_grounding(
        {"evidence_status": "incomplete", "citations": []}, set()
    )["grounded"] is True


def test_real_answer_path_opens_evidence_before_passing_rag_eval(monkeypatch):
    hit = {
        "chunk_id": "jira:DOC-1:0",
        "field": "body",
        "matched_field": "body",
        "excerpt": "The queue was saturated.",
        "external_id": "DOC-1",
        "matched_external_id": "DOC-1",
        "title": "Queue incident",
        "source": "jira",
        "author": "Ada",
        "matched_author": "Ada",
        "container": "Nimbus",
        "created_at": None,
        "updated_at": None,
        "matched_created_at": None,
        "matched_updated_at": None,
        "url": "https://jira.test/DOC-1",
        "chunk_index": 0,
        "score": 0.1,
    }
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [hit])
    monkeypatch.setattr(
        answer_module, "read_chunk", lambda *_args: {**hit, "text": hit["excerpt"]}
    )
    responses = iter([
        SimpleNamespace(
            id="read",
            output=[SimpleNamespace(
                type="function_call",
                name="read_chunk",
                arguments=json.dumps({
                    "source": "jira", "chunk_id": hit["chunk_id"]
                }),
                call_id="call-read",
            )],
            output_text="",
        ),
        SimpleNamespace(
            id="final",
            output=[],
            output_text=json.dumps({
                "answer": "The queue was saturated.",
                "evidence_status": "complete",
                "citations": [hit["chunk_id"]],
            }),
        ),
    ])
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_request: next(responses))
    )

    answer = answer_module.answer_question(
        None, "user", "Why was it slow?", lambda _query: None, client
    )

    assert evaluate_grounding(
        answer, {("jira", "jira:DOC-1:0")}
    )["grounded"] is True
