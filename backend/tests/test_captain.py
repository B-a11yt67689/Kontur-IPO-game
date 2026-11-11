"""Captain assignment through the HTTP API and PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from app.database import create_database_engine
from app.main import app


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as http_client:
        yield http_client


def create_session(client: TestClient) -> str:
    response = client.post("/sessions", json={"name": "IPO team"})
    assert response.status_code == 201
    return response.json()["id"]


def join_session(client: TestClient, session_id: str, name: str) -> dict[str, str]:
    response = client.post(
        f"/sessions/{session_id}/participants", json={"display_name": name}
    )
    assert response.status_code == 201
    return response.json()


def stored_captain(database_url: str, session_id: str) -> str | None:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("""
                    SELECT CAST(captain_participant_id AS text) FROM game_sessions
                    WHERE id = :session_id
                """),
                {"session_id": session_id},
            ).scalar_one()
    finally:
        engine.dispose()


def test_admin_assigns_captain_and_can_repeat_assignment(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    participant_id = join_session(client, session_id, "Кандидат")["id"]
    assert client.post(f"/sessions/{session_id}/preparation/start").status_code == 200

    path = f"/sessions/{session_id}/preparation/captain"
    payload = {"participant_id": participant_id}
    expected = {"session_id": session_id, "captain_participant_id": participant_id}
    first = client.put(path, json=payload)
    assert first.status_code == 200
    assert first.json() == expected
    assert stored_captain(migrated_database, session_id) == participant_id

    retry = client.put(path, json=payload)
    assert retry.status_code == 200
    assert retry.json() == expected
    assert stored_captain(migrated_database, session_id) == participant_id


def test_assignment_requires_admin_in_preparation(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    first = join_session(client, session_id, "Первый")
    second = join_session(client, session_id, "Второй")
    path = f"/sessions/{session_id}/preparation/captain"
    assert client.put(path, json={"participant_id": first["id"]}).status_code == 409
    assert stored_captain(migrated_database, session_id) is None

    assert client.post(f"/sessions/{session_id}/preparation/start").status_code == 200
    assert client.put(path, json={"participant_id": first["id"]}).status_code == 200

    payload = {"participant_id": second["id"]}
    admin_authorization = client.headers.pop("Authorization")
    try:
        assert client.put(path, json=payload).status_code == 401
    finally:
        client.headers["Authorization"] = admin_authorization
    participant_headers = {
        "Authorization": f"Bearer {second['participant_credential']}"
    }
    assert (
        client.put(path, json=payload, headers=participant_headers).status_code == 401
    )
    assert stored_captain(migrated_database, session_id) == first["id"]

    # Set a later phase directly because this task adds no later phase command.
    engine = create_database_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE game_sessions SET status = 'running' WHERE id = :id"),
                {"id": session_id},
            )
    finally:
        engine.dispose()
    assert client.put(path, json=payload).status_code == 409
    assert client.put(path, json={"participant_id": first["id"]}).status_code == 409
    assert stored_captain(migrated_database, session_id) == first["id"]


def test_assignment_rejects_foreign_and_missing_candidates(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    other_session_id = create_session(client)
    captain_id = join_session(client, session_id, "Капитан")["id"]
    foreign_id = join_session(client, other_session_id, "Другой участник")["id"]
    assert client.post(f"/sessions/{session_id}/preparation/start").status_code == 200
    path = f"/sessions/{session_id}/preparation/captain"
    assert client.put(path, json={"participant_id": captain_id}).status_code == 200

    for candidate_id in (foreign_id, str(uuid4())):
        response = client.put(path, json={"participant_id": candidate_id})
        assert response.status_code == 404
        assert response.json() == {"detail": "Participant not found"}
        assert stored_captain(migrated_database, session_id) == captain_id
    assert stored_captain(migrated_database, other_session_id) is None

    unknown_session = client.put(
        f"/sessions/{uuid4()}/preparation/captain", json={"participant_id": captain_id}
    )
    assert unknown_session.status_code == 404
    assert unknown_session.json() == {"detail": "Session not found"}


def wait_for_blocked_request(connection: Connection, blocker_pid: int) -> int:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        waiter_pid = connection.scalar(
            text("""
                SELECT DISTINCT pid FROM pg_locks
                WHERE NOT granted AND :blocker_pid = ANY(pg_blocking_pids(pid))
            """),
            {"blocker_pid": blocker_pid},
        )
        if waiter_pid is not None:
            return waiter_pid
        sleep(0.02)
    pytest.fail(f"No PostgreSQL request waited behind backend {blocker_pid}")


def test_concurrent_assignments_persist_second_candidate_in_lock_order(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    first_id = join_session(client, session_id, "Первый")["id"]
    second_id = join_session(client, session_id, "Второй")["id"]
    assert client.post(f"/sessions/{session_id}/preparation/start").status_code == 200
    path = f"/sessions/{session_id}/preparation/captain"

    engine = create_database_engine(migrated_database)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            with engine.begin() as connection:
                connection.execute(
                    text("SELECT id FROM game_sessions WHERE id = :id FOR UPDATE"),
                    {"id": session_id},
                )
                blocker_pid = connection.scalar(text("SELECT pg_backend_pid()"))
                first = executor.submit(
                    client.put, path, json={"participant_id": first_id}
                )
                first_pid = wait_for_blocked_request(connection, blocker_pid)
                second = executor.submit(
                    client.put, path, json={"participant_id": second_id}
                )
                # Observe the lock queue before the control transaction releases it.
                wait_for_blocked_request(connection, first_pid)

            for request, candidate_id in ((first, first_id), (second, second_id)):
                response = request.result(timeout=5)
                assert response.status_code == 200
                assert response.json() == {
                    "session_id": session_id,
                    "captain_participant_id": candidate_id,
                }
        assert stored_captain(migrated_database, session_id) == second_id
    finally:
        engine.dispose()
