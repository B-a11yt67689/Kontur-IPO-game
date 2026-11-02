"""Cross-feature browser-like flow against the real PostgreSQL-backed API."""

import hashlib
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_database_engine
from app.main import app
from app.models import GameSession, Participant


@pytest.fixture
def database_url_for_test(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    yield migrated_database


def _join(client: TestClient, session_id: UUID, display_name: str) -> dict[str, str]:
    response = client.post(
        f"/sessions/{session_id}/participants",
        json={"display_name": display_name},
    )
    assert response.status_code == 201
    return response.json()


def _identity(
    client: TestClient, session_id: UUID, participant: dict[str, str]
) -> dict:
    response = client.get(
        f"/sessions/{session_id}/participants/me",
        headers={"Authorization": f"Bearer {participant['participant_credential']}"},
    )
    assert response.status_code == 200
    return response.json()


def test_two_session_create_join_refresh_reconnect_and_start(
    database_url_for_test: str, admin_headers: dict[str, str]
) -> None:
    # Keep one app lifespan active; each additional TestClient is an independent
    # browser with its own headers and client-side state.
    with TestClient(app, headers=admin_headers) as admin_browser:
        first_session_response = admin_browser.post(
            "/sessions", json={"name": "North team"}
        )
        second_session_response = admin_browser.post(
            "/sessions", json={"name": "South team"}
        )
        assert first_session_response.status_code == 201
        assert second_session_response.status_code == 201
        first_session = UUID(first_session_response.json()["id"])
        second_session = UUID(second_session_response.json()["id"])

        first_browser = TestClient(app)
        second_browser = TestClient(app)
        third_browser = TestClient(app)
        second_session_browser = TestClient(app)

        first = _join(first_browser, first_session, "Ada")
        second = _join(second_browser, first_session, "Lin")
        other_session_participant = _join(
            second_session_browser, second_session, "Grace"
        )

        for browser, session_id, participant in (
            (TestClient(app), first_session, first),
            (TestClient(app), first_session, second),
            (TestClient(app), second_session, other_session_participant),
        ):
            assert (
                _identity(browser, session_id, participant)["id"] == participant["id"]
            )

        wrong_session = first_browser.get(
            f"/sessions/{second_session}/participants/me",
            headers={"Authorization": f"Bearer {first['participant_credential']}"},
        )
        assert wrong_session.status_code == 401

        first_ws_url = f"/sessions/{first_session}/lobby/ws"
        second_ws_url = f"/sessions/{second_session}/lobby/ws"
        with (
            first_browser.websocket_connect(
                first_ws_url,
                headers={"Authorization": f"Bearer {first['participant_credential']}"},
            ) as first_websocket,
            second_session_browser.websocket_connect(
                second_ws_url,
                headers={
                    "Authorization": (
                        f"Bearer {other_session_participant['participant_credential']}"
                    )
                },
            ) as second_websocket,
        ):
            first_initial = {
                "id": str(first_session),
                "name": "North team",
                "status": "lobby",
                "participants": [
                    {"id": first["id"], "display_name": "Ada"},
                    {"id": second["id"], "display_name": "Lin"},
                ],
            }
            second_initial = {
                "id": str(second_session),
                "name": "South team",
                "status": "lobby",
                "participants": [
                    {
                        "id": other_session_participant["id"],
                        "display_name": "Grace",
                    }
                ],
            }
            assert first_websocket.receive_json() == first_initial
            assert second_websocket.receive_json() == second_initial

            third = _join(third_browser, first_session, "Katherine")
            first_updated = {
                **first_initial,
                "participants": [
                    *first_initial["participants"],
                    {"id": third["id"], "display_name": "Katherine"},
                ],
            }
            assert first_websocket.receive_json() == first_updated

            first_started = admin_browser.post(
                f"/sessions/{first_session}/preparation/start"
            )
            second_started = admin_browser.post(
                f"/sessions/{second_session}/preparation/start"
            )
            assert first_started.status_code == 200
            assert second_started.status_code == 200
            assert first_started.json()["status"] == "preparation"
            assert second_started.json()["status"] == "preparation"

            first_preparation = {
                **first_updated,
                "status": "preparation",
            }
            second_preparation = {
                **second_initial,
                "status": "preparation",
            }
            assert first_websocket.receive_json() == first_preparation
            assert second_websocket.receive_json() == second_preparation

            # Generate another real update on each session so cross-session
            # messages from either start cannot hide behind an empty queue.
            assert (
                admin_browser.post(
                    f"/sessions/{first_session}/preparation/start"
                ).json()
                == first_started.json()
            )
            assert (
                admin_browser.post(
                    f"/sessions/{second_session}/preparation/start"
                ).json()
                == second_started.json()
            )
            assert first_websocket.receive_json() == first_preparation
            assert second_websocket.receive_json() == second_preparation

        second_lobby = second_session_browser.get(
            f"/sessions/{second_session}/lobby",
            headers={
                "Authorization": (
                    f"Bearer {other_session_participant['participant_credential']}"
                )
            },
        )
        assert second_lobby.status_code == 200
        assert second_lobby.json() == second_preparation

    engine = create_database_engine(database_url_for_test)
    try:
        with Session(engine) as session:
            persisted_sessions = {
                item.id: item.status
                for item in session.scalars(
                    select(GameSession).where(
                        GameSession.id.in_([first_session, second_session])
                    )
                )
            }
            persisted_participants = list(
                session.scalars(
                    select(Participant).where(
                        Participant.session_id.in_([first_session, second_session])
                    )
                )
            )
    finally:
        engine.dispose()

    assert persisted_sessions == {
        first_session: "preparation",
        second_session: "preparation",
    }
    assert {item.session_id for item in persisted_participants} == {
        first_session,
        second_session,
    }
    credentials = {
        participant["id"]: participant["participant_credential"]
        for participant in (first, second, third, other_session_participant)
    }
    assert len(persisted_participants) == 4
    for participant in persisted_participants:
        assert (
            participant.credential_digest
            == hashlib.sha256(
                credentials[str(participant.id)].encode("utf-8")
            ).hexdigest()
        )
