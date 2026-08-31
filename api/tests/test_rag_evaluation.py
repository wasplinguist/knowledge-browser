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

