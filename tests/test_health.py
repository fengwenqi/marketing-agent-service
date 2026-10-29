from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_service_metadata() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "name": "marketing-agent-service",
        "status": "ok",
        "version": "0.1.0",
    }

