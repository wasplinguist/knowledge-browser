import pytest

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
