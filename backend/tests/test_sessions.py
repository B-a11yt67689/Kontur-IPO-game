"""API integration tests against the migrated, dedicated PostgreSQL database."""

from collections.abc import Iterator
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import create_database_engine
from app.main import app
from app.models import GameSession


@pytest.fixture
def client(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as client:
        yield client


def stored_rows(value: str, name: str) -> list:
    # A fresh engine ensures this reader cannot reuse the API's connection.
    engine = create_database_engine(value)
    try:
        with engine.connect() as connection:
            return connection.execute(
                select(GameSession.__table__).where(GameSession.name == name)
            ).all()
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", ["Команда IPO", "я" * 255, "🚀" * 255])
def test_creation_committed_before_response(
    client: TestClient, migrated_database: str, name: str
) -> None:
    response = client.post("/sessions", json={"name": " \t" + name + "\n "})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "status", "created_at"}
    assert UUID(body["id"])
    assert body["name"] == name
    assert body["status"] == "lobby"
    created_at = datetime.fromisoformat(body["created_at"])
    assert created_at.utcoffset() is not None
    rows = [
        row
        for row in stored_rows(migrated_database, name)
        if row.id == UUID(body["id"])
    ]
    assert len(rows) == 1
    assert tuple(rows[0]) == (UUID(body["id"]), name, "lobby", created_at)


@pytest.mark.parametrize("name", ["", " \t\n", "\u2003\u00a0", "я" * 256])
def test_invalid_names_not_persisted(
    client: TestClient, migrated_database: str, name: str
) -> None:
    assert client.post("/sessions", json={"name": name}).status_code == 422
    assert stored_rows(migrated_database, name) == []


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"name": 42}])
def test_name_required_and_string(client: TestClient, payload: dict) -> None:
    assert client.post("/sessions", json=payload).status_code == 422


def test_duplicate_names_have_distinct_ids(
    client: TestClient, migrated_database: str
) -> None:
    name = str(uuid4())
    responses = [client.post("/sessions", json={"name": name}) for _ in range(2)]
    assert all(response.status_code == 201 for response in responses)
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 2
    assert {str(row.id) for row in stored_rows(migrated_database, name)} == ids


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", str(uuid4())),
        ("status", "running"),
        ("created_at", "2000-01-01T00:00:00Z"),
    ],
)
def test_server_owned_fields_rejected(
    client: TestClient, migrated_database: str, field: str, value: str
) -> None:
    name = str(uuid4())
    response = client.post("/sessions", json={"name": name, field: value})
    assert response.status_code == 422
    assert stored_rows(migrated_database, name) == []


@pytest.mark.parametrize("phase", ["write", "commit"])
def test_database_failure_is_sanitized_and_rolled_back(
    client: TestClient, migrated_database: str, phase: str
) -> None:
    name = str(uuid4())
    engine = create_database_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                CREATE FUNCTION ipo003_reject_session() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'private SQL/DSN/traceback sentinel';
                END; $$
            """)
            )
            # Deferred trigger runs at COMMIT, after flush and DTO creation.
            trigger = (
                "CREATE TRIGGER ipo003_reject BEFORE INSERT ON game_sessions "
                if phase == "write"
                else "CREATE CONSTRAINT TRIGGER ipo003_reject AFTER INSERT "
                "ON game_sessions DEFERRABLE INITIALLY DEFERRED "
            )
            connection.execute(
                text(trigger + "FOR EACH ROW EXECUTE FUNCTION ipo003_reject_session()")
            )
        try:
            response = client.post("/sessions", json={"name": name})
            assert response.status_code == 503
            assert response.json() == {"detail": "Session creation unavailable"}
            assert stored_rows(migrated_database, name) == []
            assert client.get("/health").json() == {"status": "ok"}
        finally:
            with engine.begin() as connection:
                connection.execute(text("DROP TRIGGER ipo003_reject ON game_sessions"))
                connection.execute(text("DROP FUNCTION ipo003_reject_session()"))
        # The failed request has released its transaction and connections.
        assert client.post("/sessions", json={"name": name}).status_code == 201
        assert len(stored_rows(migrated_database, name)) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        None,
        "invalid",
        "postgresql+psycopg://ipo002_test@127.0.0.1:1/no_db?connect_timeout=1",
    ],
)
def test_database_configuration_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, url: str | None, admin_headers: dict[str, str]
) -> None:
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)
    with TestClient(app, headers=admin_headers) as client:
        response = client.post("/sessions", json={"name": "example"})
        assert response.status_code == 503
        assert response.json() == {"detail": "Session creation unavailable"}
