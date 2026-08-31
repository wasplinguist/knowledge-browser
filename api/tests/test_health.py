from fastapi.testclient import TestClient

from knowledge_browser.main import create_app


def test_health_is_small_and_has_no_database_dependency():
    response = TestClient(create_app()).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
