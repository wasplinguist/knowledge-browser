from contextlib import contextmanager
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import knowledge_browser.main as main_module
from knowledge_browser.main import create_app


pytestmark = pytest.mark.integration

COMPANY_USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000004"


def _client(db, embed=lambda _query: None, answer_client=None):
    @contextmanager
    def connection_factory():
        yield db

    return TestClient(create_app(
        connection_factory=connection_factory,
        embed=embed,
        answer_client=answer_client,
    ))


def test_search_returns_shared_results_facets_and_safe_event(db):
    client = _client(db)

    response = client.get(
        "/api/search",
        params={"q": " Company "},
        headers={
            "X-Demo-User-Id": COMPANY_USER,
            "X-Search-Session-Id": "00000000-0000-0000-0000-000000000123",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert UUID(body["search_id"])
    assert body["profile"] == "released"
    assert body["items"][0]["external_id"] == "COMPANY-1"
    assert body["facets"] == {"confluence": 0, "github": 0, "jira": 1, "slack": 0}
    event = db.execute(
        "SELECT query, normalized_query, session_id, result_ids, "
        "embedding_available, duration_ms FROM search_events WHERE id = %s",
        (body["search_id"],),
    ).fetchone()
    assert event[:3] == (
        " Company ",
        "Company",
        UUID("00000000-0000-0000-0000-000000000123"),
    )
    assert event[3] == [{"source": "jira", "external_id": "COMPANY-1"}]
    assert event[4] is False
    assert event[5] >= 1


@pytest.mark.parametrize(
    ("query", "headers", "source", "code"),
    [
        ("Company", {}, None, "missing_demo_user"),
        ("Company", {"X-Demo-User-Id": "bad"}, None, "unknown_demo_user"),
        (" ", {"X-Demo-User-Id": COMPANY_USER}, None, "invalid_query"),
        ("Company", {"X-Demo-User-Id": COMPANY_USER}, "email", "invalid_source"),
    ],
)
def test_search_rejects_invalid_requests(db, query, headers, source, code):
    response = _client(db).get(
        "/api/search", params={"q": query, "source": source}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_embedding_failure_keeps_keyword_search_available(db):
    def fail(_query):
        raise RuntimeError("provider unavailable")

    response = _client(db, fail).get(
        "/api/search",
        params={"q": "Company"},
        headers={"X-Demo-User-Id": COMPANY_USER},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["external_id"] == "COMPANY-1"


def test_analytics_failure_keeps_search_available(db):
    db.execute(
        """
        CREATE FUNCTION fail_search_event_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'event insert unavailable';
        END;
        $$;
        CREATE TRIGGER fail_search_event_insert BEFORE INSERT ON search_events
        FOR EACH ROW EXECUTE FUNCTION fail_search_event_insert();
        """
    )

    response = _client(db).get(
        "/api/search",
        params={"q": "Company"},
        headers={"X-Demo-User-Id": COMPANY_USER},
    )

    assert response.status_code == 200
    assert response.json()["search_id"] is None
    assert response.json()["items"][0]["external_id"] == "COMPANY-1"


def test_demo_users_exposes_only_demo_identity_fields(db):
    response = _client(db).get("/api/demo-users")

    assert response.status_code == 200
    assert set(response.json()["items"][0]) == {"id", "email", "name"}


def test_click_is_owner_only_and_must_match_the_stored_rank(db):
    client = _client(db)
    search = client.get(
        "/api/search",
        params={"q": "Company"},
        headers={"X-Demo-User-Id": COMPANY_USER},
    ).json()
    payload = {"source": "jira", "external_id": "COMPANY-1", "rank": 1}

    denied = client.post(
        f"/api/search-events/{search['search_id']}/click",
        headers={"X-Demo-User-Id": OTHER_USER},
        json=payload,
    )
    wrong_rank = client.post(
        f"/api/search-events/{search['search_id']}/click",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json={**payload, "rank": 2},
    )
    allowed = client.post(
        f"/api/search-events/{search['search_id']}/click",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json=payload,
    )
    duplicate = client.post(
        f"/api/search-events/{search['search_id']}/click",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json=payload,
    )

    assert denied.status_code == wrong_rank.status_code == 404
    assert allowed.status_code == duplicate.status_code == 204
    assert db.execute("SELECT count(*) FROM search_clicks").fetchone()[0] == 1


def test_answer_route_uses_demo_identity_and_returns_grounded_shape(db):
    class Responses:
        def create(self, **_request):
            return SimpleNamespace(
                id="final",
                output=[],
                output_text=json.dumps({
                    "answer": "No opened evidence yet.",
                    "evidence_status": "incomplete",
                    "citations": [],
                }),
            )

    response = _client(
        db, answer_client=SimpleNamespace(responses=Responses())
    ).post(
        "/api/answer",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json={"question": "Who owns Company?", "mode": "fast", "debug": True},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "fast"
    assert response.json()["evidence_status"] == "incomplete"
    assert response.json()["trace"] == []


def test_answer_route_uses_lazy_runtime_provider_when_key_exists(db, monkeypatch):
    class Responses:
        def create(self, **_request):
            return SimpleNamespace(
                id="final", output=[], output_text=json.dumps({
                    "answer": "No opened evidence yet.",
                    "evidence_status": "incomplete",
                    "citations": [],
                    "conflicts": [],
                    "missing_information": [],
                    "follow_ups": [],
                }),
            )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        main_module, "_default_answer_client",
        lambda: SimpleNamespace(responses=Responses()),
    )

    response = _client(db).post(
        "/api/answer",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json={"question": "Who owns Company?"},
    )

    assert response.status_code == 200
    assert response.json()["evidence_status"] == "incomplete"


def test_answer_route_rejects_unknown_mode(db):
    response = _client(db).post(
        "/api/answer",
        headers={"X-Demo-User-Id": COMPANY_USER},
        json={"question": "Who owns Company?", "mode": "slow"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
