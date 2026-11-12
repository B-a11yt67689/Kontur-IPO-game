"""Role claims through the HTTP API and PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

from app.database import create_database_engine
from app.main import app
from tests.test_captain import (
    create_session,
    join_session,
    stored_captain,
    wait_for_blocked_request,
)


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as http_client:
        yield http_client


@pytest.fixture
def engine(migrated_database: str) -> Iterator[Engine]:
    database = create_database_engine(migrated_database)
    try:
        yield database
    finally:
        database.dispose()


def participant_headers(participant: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {participant['participant_credential']}"}


def claim_role(
    client: TestClient, session_id: str, role_code: str, participant: dict[str, str]
) -> Response:
    return client.put(
        f"/sessions/{session_id}/preparation/roles/{role_code}",
        headers=participant_headers(participant),
        json={"participant_id": participant["id"]},
    )


def stored_roles(engine: Engine, session_id: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("""
                SELECT role_code, CAST(participant_id AS text) FROM role_assignments
                WHERE session_id = :id
            """),
            {"id": session_id},
        ).all()
        return dict(rows)


def start_preparation(client: TestClient, session_id: str) -> None:
    assert client.post(f"/sessions/{session_id}/preparation/start").status_code == 200


def test_claim_survives_reconnect_and_keeps_captain(
    client: TestClient, engine: Engine, migrated_database: str
) -> None:
    session_id = create_session(client)
    captain = join_session(client, session_id, "Капитан")
    members = {"cto": captain}
    for role_code in ("cmo", "people", "cfo"):
        members[role_code] = join_session(client, session_id, role_code)
    start_preparation(client, session_id)
    assert (
        client.put(
            f"/sessions/{session_id}/preparation/captain",
            json={"participant_id": captain["id"]},
        ).status_code
        == 200
    )

    for role_code, participant in members.items():
        response = claim_role(client, session_id, role_code, participant)
        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "role_code": role_code,
            "participant_id": participant["id"],
        }

    with TestClient(app) as reconnected:
        identity = reconnected.get(
            f"/sessions/{session_id}/participants/me",
            headers=participant_headers(captain),
        )
        assert identity.status_code == 200
        assert identity.json()["id"] == captain["id"]
        repeat = claim_role(reconnected, session_id, "cto", captain)
        assert repeat.status_code == 200
        assert repeat.json() == {
            "session_id": session_id,
            "role_code": "cto",
            "participant_id": captain["id"],
        }

    assert stored_roles(engine, session_id) == {
        role_code: participant["id"] for role_code, participant in members.items()
    }
    assert stored_captain(migrated_database, session_id) == captain["id"]


def test_roles_are_scoped_to_authenticated_participant_and_session(
    client: TestClient, engine: Engine
) -> None:
    session_id, other_session_id = create_session(client), create_session(client)
    first = join_session(client, session_id, "Первый")
    second = join_session(client, session_id, "Второй")
    outsider = join_session(client, other_session_id, "Чужой")
    start_preparation(client, session_id)
    start_preparation(client, other_session_id)
    path = f"/sessions/{session_id}/preparation/roles/cto"

    payload = {"participant_id": first["id"]}
    with TestClient(app) as anonymous:
        response = anonymous.put(path, json=payload)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert client.put(path, json=payload).status_code == 401  # Admin bearer.
    assert (
        client.put(
            path, headers={"Authorization": "Bearer invalid"}, json=payload
        ).status_code
        == 401
    )
    assert claim_role(client, session_id, "cto", outsider).status_code == 401

    for unauthorized_id in (second["id"], outsider["id"], str(uuid4())):
        response = client.put(
            path,
            headers=participant_headers(first),
            json={"participant_id": unauthorized_id},
        )
        assert response.status_code == 403

    assert stored_roles(engine, session_id) == {}
    assert stored_roles(engine, other_session_id) == {}
    assert claim_role(client, session_id, "cto", first).status_code == 200
    assert claim_role(client, other_session_id, "cto", outsider).status_code == 200
    assert stored_roles(engine, session_id) == {"cto": first["id"]}
    assert stored_roles(engine, other_session_id) == {"cto": outsider["id"]}


def test_roles_reject_unknown_code_wrong_phase_and_changes(
    client: TestClient, engine: Engine
) -> None:
    session_id = create_session(client)
    first = join_session(client, session_id, "Первый")
    second = join_session(client, session_id, "Второй")
    assert claim_role(client, session_id, "cto", first).status_code == 409
    start_preparation(client, session_id)
    assert claim_role(client, session_id, "CEO", first).status_code == 422
    assert stored_roles(engine, session_id) == {}
    assert claim_role(client, session_id, "cto", first).status_code == 200
    assert claim_role(client, session_id, "cto", second).status_code == 409
    assert claim_role(client, session_id, "cfo", first).status_code == 409

    # Simulate a later phase without adding a phase transition to the API.
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE game_sessions SET status = 'running' WHERE id = :id"),
            {"id": session_id},
        )
    assert claim_role(client, session_id, "cfo", second).status_code == 409
    assert claim_role(client, session_id, "cto", first).status_code == 409
    assert stored_roles(engine, session_id) == {"cto": first["id"]}


@pytest.mark.parametrize("same_participant", [False, True])
def test_competing_claims_leave_first_assignment(
    client: TestClient, engine: Engine, same_participant: bool
) -> None:
    session_id = create_session(client)
    first = join_session(client, session_id, "Первый")
    second = first if same_participant else join_session(client, session_id, "Второй")
    second_role = "cfo" if same_participant else "cto"
    start_preparation(client, session_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM game_sessions WHERE id = :id FOR UPDATE"),
                {"id": session_id},
            )
            blocker_pid = connection.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            first_request = executor.submit(
                claim_role, client, session_id, "cto", first
            )
            first_pid = wait_for_blocked_request(connection, blocker_pid)
            second_request = executor.submit(
                claim_role, client, session_id, second_role, second
            )
            wait_for_blocked_request(connection, first_pid)

        assert first_request.result(timeout=5).status_code == 200
        assert second_request.result(timeout=5).status_code == 409
    assert stored_roles(engine, session_id) == {"cto": first["id"]}
