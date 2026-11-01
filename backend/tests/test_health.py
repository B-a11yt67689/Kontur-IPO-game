from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok_on_repeated_requests() -> None:
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/health")

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"status": "ok"}
            assert response.content == b'{"status":"ok"}'
