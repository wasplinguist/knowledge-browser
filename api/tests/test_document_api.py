from contextlib import contextmanager
import json

import pytest
from fastapi.testclient import TestClient

from knowledge_browser.main import create_app


pytestmark = pytest.mark.integration
COMPANY_USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000004"


def _client(db):
    @contextmanager
    def connection_factory():
        yield db

    return TestClient(create_app(connection_factory=connection_factory))


@pytest.mark.parametrize(
    ("source", "external_id", "payload_key", "payload_value"),
    [
        ("jira", "COMPANY-1", "status", "Open"),
        ("confluence", "PAGE-1", "space", "ENG"),
        ("slack", "THREAD-1", "channel", "#incidents"),
        ("github", "PR-1", "repository", "northstar/browser"),
    ],
)
def test_allowed_document_returns_only_source_display_payload(
    db, source, external_id, payload_key, payload_value
):
    db.execute(
        """
        UPDATE documents
        SET source = %s, external_id = %s, raw_payload = %s
        WHERE external_id = 'COMPANY-1'
        """,
        (
            source,
            external_id,
            json.dumps({"payload": {
                payload_key: payload_value,
                "description": "Visible detail",
                "acl": {"company_access": True},
                "claims": ["synthetic truth"],
                "event_ids": ["private-event"],
                "noise_label": "easy",
            }}),
        ),
    )

    response = _client(db).get(
        f"/api/documents/{source}/{external_id}",
        headers={"X-Demo-User-Id": COMPANY_USER},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == source
    assert body["external_id"] == external_id
    assert body["payload"][payload_key] == payload_value
    assert not ({"acl", "claims", "event_ids", "noise_label"} & body["payload"].keys())


def test_forbidden_and_missing_documents_have_the_same_safe_404(db):
    client = _client(db)
    forbidden = client.get(
        "/api/documents/confluence/DIRECT-1",
        headers={"X-Demo-User-Id": OTHER_USER},
    )
    missing = client.get(
        "/api/documents/confluence/DOES-NOT-EXIST",
        headers={"X-Demo-User-Id": OTHER_USER},
    )

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json() == {
        "error": {"code": "document_not_found", "message": "document is unavailable"}
    }


def test_document_endpoint_rejects_missing_identity_and_invalid_source(db):
    client = _client(db)
    missing_identity = client.get("/api/documents/jira/COMPANY-1")
    invalid_source = client.get(
        "/api/documents/email/COMPANY-1",
        headers={"X-Demo-User-Id": COMPANY_USER},
    )

    assert missing_identity.status_code == invalid_source.status_code == 400
    assert missing_identity.json()["error"]["code"] == "missing_demo_user"
    assert invalid_source.json()["error"]["code"] == "invalid_source"
