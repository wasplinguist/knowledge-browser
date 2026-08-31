import json
from types import SimpleNamespace

import pytest

import knowledge_browser.answer as answer_module


pytestmark = pytest.mark.unit


def _call(response_id, name, arguments):
    return SimpleNamespace(
        id=response_id,
        output=[SimpleNamespace(
            type="function_call",
            name=name,
            arguments=json.dumps(arguments),
            call_id=f"call-{response_id}",
        )],
        output_text="",
    )


def _final(payload):
    return SimpleNamespace(id="final", output=[], output_text=json.dumps(payload))


class Responses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


def _result(chunk_id="jira:DOC-1:0"):
    return {
        "chunk_id": chunk_id,
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


def test_answer_starts_with_shared_hybrid_results_and_cites_opened_evidence(monkeypatch):
    hit = _result()
    opened = {**hit, "text": "The queue was saturated."}
    searches = []
    monkeypatch.setattr(
        answer_module,
        "hybrid_search",
        lambda _conn, _user, query, embedding, source, profile: searches.append(
            (query, embedding, source, profile.name)
        ) or [hit],
    )
    monkeypatch.setattr(answer_module, "read_chunk", lambda *_args: opened)
    responses = Responses([
        _call("read", "read_chunk", {"source": "jira", "chunk_id": hit["chunk_id"]}),
        _final({
            "answer": "The queue was saturated.",
            "evidence_status": "complete",
            "citations": [hit["chunk_id"]],
            "conflicts": [],
            "missing_information": [],
            "follow_ups": [],
        }),
    ])

    answer = answer_module.answer_question(
        None,
        "user",
        "Why was the router slow?",
        lambda _query: [1.0],
        SimpleNamespace(responses=responses),
    )

    assert searches == [("Why was the router slow?", [1.0], None, "released")]
    assert answer["evidence_status"] == "complete"
    assert answer["citations"][0]["chunk_id"] == hit["chunk_id"]
    assert "Queue incident" in responses.requests[0]["input"][0]["content"]


def test_unopened_citation_is_removed_and_complete_is_downgraded(monkeypatch):
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [_result()])
    responses = Responses([_final({
        "answer": "Unsupported claim",
        "evidence_status": "complete",
        "citations": ["never-opened"],
    })])

    answer = answer_module.answer_question(
        None, "user", "Who owns it?", lambda _query: None,
        SimpleNamespace(responses=responses),
    )

    assert answer["citations"] == []
    assert answer["evidence_status"] == "incomplete"


def test_two_opened_conflict_citations_force_conflicting(monkeypatch):
    hits = [_result("jira:A:0"), _result("jira:B:0")]
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: hits)
    monkeypatch.setattr(
        answer_module,
        "read_chunk",
        lambda _conn, _user, _source, chunk_id: {
            **next(item for item in hits if item["chunk_id"] == chunk_id),
            "text": chunk_id,
        },
    )
    responses = Responses([
        SimpleNamespace(id="reads", output=[
            SimpleNamespace(type="function_call", name="read_chunk", arguments=json.dumps({"source": "jira", "chunk_id": item["chunk_id"]}), call_id=f"call-{index}")
            for index, item in enumerate(hits)
        ], output_text=""),
        _final({
            "answer": "The evidence conflicts.",
            "evidence_status": "complete",
            "citations": [item["chunk_id"] for item in hits],
            "conflicts": [{
                "description": "Status differs",
                "citations": [item["chunk_id"] for item in hits],
            }],
        }),
    ])

    answer = answer_module.answer_question(
        None, "user", "Compare the status", lambda _query: None,
        SimpleNamespace(responses=responses), mode="deep",
    )

    assert answer["evidence_status"] == "conflicting"
    assert len(answer["conflicts"]) == 1


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Who owns Nimbus?", "fast"),
        ("What did Slack say and what is the latest Jira status?", "deep"),
        ("List every Nimbus incident", "deep"),
    ],
)
def test_auto_mode_uses_deterministic_complexity_rules(question, expected):
    assert answer_module.route_mode(question) == expected


def test_provider_failure_returns_safe_execution_error(monkeypatch):
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [])

    class Failed:
        def create(self, **_request):
            raise RuntimeError("secret provider detail")

    with pytest.raises(answer_module.AnswerExecutionError) as raised:
        answer_module.answer_question(
            None, "user", "What happened?", lambda _query: None,
            SimpleNamespace(responses=Failed()),
        )

    assert raised.value.execution["llm_loops"] == 1


def test_fast_tool_budget_forces_a_final_response_without_tools(monkeypatch):
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [])

    class Repeating:
        def __init__(self):
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            if request.get("tool_choice") == "none":
                return _final({"answer": "No evidence", "citations": []})
            return _call(
                str(len(self.requests)),
                "hybrid_search",
                {"query": "another query", "source": None},
            )

    responses = Repeating()
    answer = answer_module.answer_question(
        None, "user", "What happened?", lambda _query: None,
        SimpleNamespace(responses=responses), mode="fast",
    )

    assert answer["execution"]["tool_calls"] == 3
    assert responses.requests[-1]["tools"] == []
    assert responses.requests[-1]["tool_choice"] == "none"


def test_tool_failure_returns_safe_partial_execution(monkeypatch):
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [_result()])
    monkeypatch.setattr(
        answer_module, "read_chunk", lambda *_args: (_ for _ in ()).throw(
            RuntimeError("database detail")
        )
    )
    responses = Responses([
        _call("read", "read_chunk", {"source": "jira", "chunk_id": "jira:DOC-1:0"})
    ])

    with pytest.raises(answer_module.AnswerExecutionError) as raised:
        answer_module.answer_question(
            None, "user", "What happened?", lambda _query: None,
            SimpleNamespace(responses=responses),
        )

    assert raised.value.execution["tool_calls"] == 1
    assert raised.value.trace[0]["status"] == "failed"


def test_invalid_final_json_is_plain_incomplete_text(monkeypatch):
    monkeypatch.setattr(answer_module, "hybrid_search", lambda *_args: [])
    response = SimpleNamespace(id="final", output=[], output_text="Plain answer")

    answer = answer_module.answer_question(
        None, "user", "What happened?", lambda _query: None,
        SimpleNamespace(responses=Responses([response])),
    )

    assert answer["answer"] == "Plain answer"
    assert answer["evidence_status"] == "incomplete"
    assert answer["citations"] == []
