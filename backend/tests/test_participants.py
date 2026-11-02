"""Participant joins and credential-based identity recovery in PostgreSQL."""

import hashlib
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_database_engine
from app.main import app
from app.models import Participant


def test_missing_credential_is_rejected_without_database() -> None:
    with TestClient(app) as client:
        response = client.get(f"/sessions/{uuid4()}/participants/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid participant credentials"}


@pytest.fixture
def client(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as test_client:
        yield test_client


def create_session(client: TestClient) -> UUID:
    response = client.post("/sessions", json={"name": "IPO team"})
    assert response.status_code == 201
    return UUID(response.json()["id"])


def stored_participants(value: str, session_id: UUID) -> list[Participant]:
    engine = create_database_engine(value)
    try:
        with Session(engine) as session:
            return list(
                session.scalars(
                    select(Participant).where(Participant.session_id == session_id)
                ).all()
            )
    finally:
        engine.dispose()


def join(client: TestClient, session_id: UUID, display_name: str) -> dict[str, str]:
    response = client.post(
        f"/sessions/{session_id}/participants",
        json={"display_name": display_name},
    )
    assert response.status_code == 201
    return response.json()


def assert_lobby_has_no_secrets(response_body: str, credentials: list[str]) -> None:
    assert "participant_credential" not in response_body
    for credential in credentials:
        assert credential not in response_body
        assert (
            hashlib.sha256(credential.encode("utf-8")).hexdigest() not in response_body
        )


def test_join_persists_and_recovers_identity(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    joined = join(client, session_id, "  Команда  ")

    assert set(joined) == {
        "id",
        "display_name",
        "participant_credential",
        "token_type",
    }
    assert joined["display_name"] == "Команда"
    assert joined["token_type"] == "bearer"
    participant_id = UUID(joined["id"])
    credential = joined["participant_credential"]
    assert len(credential) >= 40

    rows = stored_participants(migrated_database, session_id)
    assert len(rows) == 1
    assert rows[0].id == participant_id
    assert rows[0].display_name == "Команда"
    assert (
        rows[0].credential_digest
        == hashlib.sha256(credential.encode("utf-8")).hexdigest()
    )
    with TestClient(app) as refreshed_client:
        restored = refreshed_client.get(
            f"/sessions/{session_id}/participants/me",
            headers={"Authorization": f"Bearer {credential}"},
        )
    assert restored.status_code == 200
    assert restored.json()["id"] == str(participant_id)
    assert restored.json()["display_name"] == "Команда"
    assert "participant_credential" not in restored.json()


def test_duplicate_display_names_have_separate_identities(
    client: TestClient, migrated_database: str
) -> None:
    session_id = create_session(client)
    first = join(client, session_id, "Analyst")
    second = join(client, session_id, "Analyst")

    assert first["id"] != second["id"]
    assert first["participant_credential"] != second["participant_credential"]
    assert len(stored_participants(migrated_database, session_id)) == 2
    for participant in (first, second):
        response = client.get(
            f"/sessions/{session_id}/participants/me",
            headers={
                "Authorization": f"Bearer {participant['participant_credential']}"
            },
        )
        assert response.status_code == 200
        assert response.json()["id"] == participant["id"]


def test_missing_wrong_and_cross_session_credentials_are_rejected(
    client: TestClient,
) -> None:
    first_session = create_session(client)
    second_session = create_session(client)
    joined = join(client, first_session, "Participant")
    url = f"/sessions/{first_session}/participants/me"

    client.headers.pop("Authorization", None)
    assert client.get(url).status_code == 401
    assert (
        client.get(
            url, headers={"Authorization": "Bearer not-a-valid-credential"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/sessions/{second_session}/participants/me",
            headers={"Authorization": f"Bearer {joined['participant_credential']}"},
        ).status_code
        == 401
    )


def test_unknown_session_cannot_be_joined(client: TestClient) -> None:
    response = client.post(
        f"/sessions/{uuid4()}/participants",
        json={"display_name": "Participant"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_lobby_snapshot_has_exact_shape_and_current_session_state(
    client: TestClient,
) -> None:
    session_id = create_session(client)
    participant = join(client, session_id, "Analyst")

    response = client.get(
        f"/sessions/{session_id}/lobby",
        headers={"Authorization": f"Bearer {participant['participant_credential']}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(session_id),
        "name": "IPO team",
        "status": "lobby",
        "participants": [
            {"id": participant["id"], "display_name": "Analyst"},
        ],
    }
    assert_lobby_has_no_secrets(response.text, [participant["participant_credential"]])


def test_lobby_reconnect_shows_participants_who_joined_since_last_request(
    client: TestClient,
) -> None:
    session_id = create_session(client)
    first = join(client, session_id, "First")
    second = join(client, session_id, "Second")
    expected_participants = [
        {"id": first["id"], "display_name": "First"},
        {"id": second["id"], "display_name": "Second"},
    ]

    for participant in (first, second):
        with TestClient(app) as reconnected_client:
            response = reconnected_client.get(
                f"/sessions/{session_id}/lobby",
                headers={
                    "Authorization": (f"Bearer {participant['participant_credential']}")
                },
            )

        assert response.status_code == 200
        assert response.json()["participants"] == expected_participants
        assert_lobby_has_no_secrets(
            response.text,
            [first["participant_credential"], second["participant_credential"]],
        )


def test_lobby_requires_a_credential_scoped_to_the_session(
    client: TestClient,
) -> None:
    first_session = create_session(client)
    second_session = create_session(client)
    first = join(client, first_session, "First")
    second = join(client, second_session, "Second")
    lobby_url = f"/sessions/{first_session}/lobby"

    for headers in (
        {},
        {"Authorization": "Bearer invalid-credential"},
        {"Authorization": f"Bearer {second['participant_credential']}"},
    ):
        response = client.get(lobby_url, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid participant credentials"}

    unknown = client.get(
        f"/sessions/{uuid4()}/lobby",
        headers={"Authorization": f"Bearer {first['participant_credential']}"},
    )
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Session not found"}


@pytest.mark.parametrize("display_name", ["", "  ", "я" * 256])
def test_invalid_display_name_is_rejected(
    client: TestClient, migrated_database: str, display_name: str
) -> None:
    session_id = create_session(client)
    response = client.post(
        f"/sessions/{session_id}/participants",
        json={"display_name": display_name},
    )
    assert response.status_code == 422
    assert stored_participants(migrated_database, session_id) == []
