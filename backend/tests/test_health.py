"""Health endpoint tests. These do not call external services."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_http_200() -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_status_is_ok() -> None:
    response = client.get("/health")
    assert response.json()["status"] == "ok"
