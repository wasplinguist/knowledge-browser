import pytest
from fastapi.testclient import TestClient

from knowledge_browser.main import create_app


pytestmark = pytest.mark.unit


def test_health_is_small_and_has_no_database_dependency():
    response = TestClient(create_app()).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_only_approved_feature_routes_are_exposed():
    app = create_app()

    assert sorted(route.path for route in app.routes) == [
        "/api/demo-users",
        "/api/health",
        "/api/search",
        "/api/search-events/{search_id}/click",
    ]
