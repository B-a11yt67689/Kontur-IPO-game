import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    "url", [None, "invalid", "postgresql+psycopg://localhost:1/no_db"]
)
def test_health_returns_ok_on_repeated_requests(
    monkeypatch: pytest.MonkeyPatch, url: str | None
) -> None:
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/health")

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"status": "ok"}
            assert response.content == b'{"status":"ok"}'
