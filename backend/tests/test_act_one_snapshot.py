"""Authorize and read saved first-act snapshots on real PostgreSQL."""

import hashlib
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.database import create_database_engine
from app.main import app
from app.models import ActOneState, GameSession
from tests.test_act_one_start import client as client
from tests.test_act_one_start import engine as engine
from tests.test_act_one_start import prepared_team as prepared_team
from tests.test_act_one_start import start_path, stored_state
from tests.test_captain import create_session, join_session
from tests.test_roles import participant_headers


def snapshot_path(session_id):
    return f"/sessions/{session_id}/acts/1"


def test_snapshot_survives_reconnect_and_reflects_saved_purchase(
    client, engine, prepared_team
):
    session_id, members = prepared_team
    # A participant without a role is entitled to the same snapshot.
    spectator = join_session(client, session_id, "Без роли")
    started = client.post(start_path(session_id))
    assert started.status_code == 200
    expected = started.json()
    response = client.get(
        snapshot_path(session_id), headers=participant_headers(spectator)
    )
    assert response.status_code == 200
    assert response.json() == expected
    for member in [*members, spectator]:
        credential = member["participant_credential"]
        assert credential not in response.text
        assert hashlib.sha256(credential.encode()).hexdigest() not in response.text
    assert "credential" not in response.text
    assert "digest" not in response.text

    with TestClient(app) as reconnected:
        reconnect = reconnected.get(
            snapshot_path(session_id), headers=participant_headers(members[0])
        )
        assert reconnect.status_code == 200
        assert reconnect.json() == expected

    with engine.begin() as connection:
        connection.execute(
            ActOneState.__table__.update()
            .where(ActOneState.session_id == UUID(session_id))
            .values(hiring_level=1, hiring_capacity=3, action_points=100)
        )
    saved_purchase = client.get(
        snapshot_path(session_id), headers=participant_headers(spectator)
    )
    assert saved_purchase.status_code == 200
    assert saved_purchase.json() == {
        **expected,
        "hiring_level": 1,
        "hiring_capacity": 3,
        "action_points": 100,
    }


def test_snapshot_isolates_sessions_and_authorizes_before_phase(
    client, engine, prepared_team
):
    first_session, members = prepared_team
    other_session = create_session(client)
    outsider = join_session(client, other_session, "Другая команда")
    assert client.post(start_path(first_session)).status_code == 200
    first_headers = participant_headers(members[0])
    other_headers = participant_headers(outsider)
    with TestClient(app) as anonymous:
        attempts = [
            anonymous.get(snapshot_path(first_session)),
            client.get(snapshot_path(first_session)),  # Administrator JWT.
            anonymous.get(
                snapshot_path(first_session),
                headers={"Authorization": "Bearer invalid"},
            ),
            anonymous.get(snapshot_path(first_session), headers=other_headers),
            anonymous.get(snapshot_path(other_session), headers=first_headers),
            anonymous.get(snapshot_path(str(uuid4())), headers=first_headers),
        ]
    for denied in attempts:
        assert denied.status_code == 401
        assert denied.json() == {"detail": "Invalid credentials"}
        assert denied.headers["WWW-Authenticate"] == "Bearer"

    # A member can distinguish their own phase, but outsiders cannot inspect it.
    assert (
        client.get(snapshot_path(other_session), headers=other_headers).status_code
        == 409
    )
    assert (
        client.get(snapshot_path(first_session), headers=first_headers).status_code
        == 200
    )
    with engine.begin() as connection:
        connection.execute(
            GameSession.__table__.update()
            .where(GameSession.id == UUID(first_session))
            .values(status="finished")
        )
    assert (
        client.get(snapshot_path(first_session), headers=first_headers).status_code
        == 409
    )
    assert (
        client.get(snapshot_path(first_session), headers=other_headers).status_code
        == 401
    )


@pytest.mark.parametrize("phase", ["preparation", "act_one"])
def test_snapshot_with_no_state_is_conflict_only_for_a_member(
    client, engine, prepared_team, phase
):
    session_id, members = prepared_team
    with engine.begin() as connection:
        connection.execute(
            GameSession.__table__.update()
            .where(GameSession.id == UUID(session_id))
            .values(status=phase)
        )
    response = client.get(
        snapshot_path(session_id), headers=participant_headers(members[0])
    )
    assert response.status_code == 409
    assert stored_state(engine, session_id) is None
    assert client.get(snapshot_path(session_id)).status_code == 401


def test_read_uses_one_select_without_rng_or_mutations(
    client, migrated_database, prepared_team, monkeypatch
):
    session_id, members = prepared_team
    started = client.post(start_path(session_id))
    assert started.status_code == 200
    engine = create_database_engine(migrated_database)
    before = stored_state(engine, session_id)
    random = Mock(side_effect=AssertionError("Read must not roll"))
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    try:
        # Initialize the dialect before observing SQL generated by GET itself.
        with engine.connect():
            pass
        statements = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda _connection, _cursor, statement, *_: statements.append(statement),
        )
        monkeypatch.setattr("app.act_one.create_database_engine", lambda: engine)
        response = client.get(
            snapshot_path(session_id), headers=participant_headers(members[0])
        )
        assert response.status_code == 200
        assert response.json() == started.json()
        assert len(statements) == 1
        assert statements[0].lstrip().upper().startswith("SELECT ")
        assert "EXISTS" in statements[0].upper()
        assert "LEFT OUTER JOIN" in statements[0].upper()
        assert "FOR UPDATE" not in statements[0].upper()
        random.assert_not_called()
        assert stored_state(engine, session_id) == before
    finally:
        engine.dispose()


def test_missing_bearer_is_rejected_without_database(monkeypatch):
    database = Mock(side_effect=AssertionError("Missing bearer accessed DB"))
    monkeypatch.setattr("app.act_one.create_database_engine", database)
    with TestClient(app) as anonymous:
        response = anonymous.get(snapshot_path(str(uuid4())))
    assert response.status_code == 401
    database.assert_not_called()


@pytest.mark.parametrize("failure", ["configuration", "connection", "query"])
def test_snapshot_database_failure_is_sanitized(
    client, prepared_team, engine, monkeypatch, failure
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    if failure == "configuration":
        monkeypatch.setattr(
            "app.act_one.create_database_engine",
            Mock(side_effect=ValueError("private DSN sentinel")),
        )
    elif failure == "connection":
        monkeypatch.setattr(
            "app.act_one.create_database_engine",
            Mock(
                side_effect=OperationalError(
                    "private SQL", {}, Exception("private DSN")
                )
            ),
        )
    else:

        def fail_query(*_args):
            raise OperationalError("private SQL sentinel", {}, Exception("private DSN"))

        event.listen(engine, "before_cursor_execute", fail_query)
        monkeypatch.setattr("app.act_one.create_database_engine", lambda: engine)
    response = client.get(
        snapshot_path(session_id), headers=participant_headers(members[0])
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Act one unavailable"}
    assert "sentinel" not in response.text
