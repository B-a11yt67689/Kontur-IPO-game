"""Preparation snapshots through the HTTP API and PostgreSQL."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.database import create_database_engine
from app.main import app
from tests.test_captain import create_session, join_session
from tests.test_roles import claim_role, participant_headers, start_preparation


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as http_client:
        yield http_client


def test_preparation_reflects_committed_assignments_on_reconnect(
    client: TestClient,
) -> None:
    session_id = create_session(client)
    first = join_session(client, session_id, "Первый")
    second = join_session(client, session_id, "Второй")
    start_preparation(client, session_id)
    path = f"/sessions/{session_id}/preparation"
    headers = participant_headers(second)

    initial = client.get(path, headers=headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "id": session_id,
        "name": "IPO team",
        "status": "preparation",
        "participants": [
            {"id": first["id"], "display_name": "Первый"},
            {"id": second["id"], "display_name": "Второй"},
        ],
        "captain_participant_id": None,
        "roles": {"cto": None, "cmo": None, "people": None, "cfo": None},
    }

    captain = client.put(f"{path}/captain", json={"participant_id": second["id"]})
    assert captain.status_code == 200
    assert claim_role(client, session_id, "cto", first).status_code == 200
    assert claim_role(client, session_id, "people", second).status_code == 200

    with TestClient(app) as reconnected:
        snapshot = reconnected.get(path, headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json() == {
        **initial.json(),
        "captain_participant_id": second["id"],
        "roles": {"cto": first["id"], "cmo": None, "people": second["id"], "cfo": None},
    }


def test_preparation_authenticates_before_checking_phase(
    client: TestClient, migrated_database: str
) -> None:
    session_id, other_session_id = create_session(client), create_session(client)
    member = join_session(client, session_id, "Участник")
    outsider = join_session(client, other_session_id, "Другая команда")
    path = f"/sessions/{session_id}/preparation"
    headers = participant_headers(member)
    outsider_headers = participant_headers(outsider)
    with TestClient(app) as anonymous:
        for response in (
            anonymous.get(path),
            client.get(path),  # Admin bearer.
            anonymous.get(path, headers={"Authorization": "Bearer invalid"}),
            anonymous.get(path, headers=outsider_headers),
            anonymous.get(f"/sessions/{uuid4()}/preparation", headers=headers),
        ):
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.json() == {"detail": "Invalid credentials"}

    lobby = client.get(path, headers=headers)
    assert lobby.status_code == 409
    assert lobby.json() == {"detail": "Session is not in preparation"}
    start_preparation(client, session_id)
    assert client.get(path, headers=outsider_headers).status_code == 401
    assert client.get(path, headers=headers).status_code == 200

    engine = create_database_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE game_sessions SET status = 'running' WHERE id = :id"),
                {"id": session_id},
            )
    finally:
        engine.dispose()
    assert client.get(path, headers=headers).status_code == 409


def test_preparation_reads_authorization_and_snapshot_in_one_statement(
    client: TestClient, migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = create_session(client)
    member = join_session(client, session_id, "Участник")
    start_preparation(client, session_id)

    engine = create_database_engine(migrated_database)
    try:
        # Connect before recording statements so dialect initialization is excluded.
        with engine.connect():
            pass
        statements: list[str] = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda _connection, _cursor, statement, *_: statements.append(statement),
        )
        monkeypatch.setattr("app.main.create_database_engine", lambda: engine)
        response = client.get(
            f"/sessions/{session_id}/preparation",
            headers=participant_headers(member),
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT ")
