"""First hiring upgrade authorization and transactions on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.models import ActOneState, GameSession
from tests.test_act_one_snapshot import snapshot_path
from tests.test_act_one_start import _valid_state_values, start_path, stored_state
from tests.test_act_one_start import client as client
from tests.test_act_one_start import engine as engine
from tests.test_act_one_start import prepared_team as prepared_team
from tests.test_captain import create_session, join_session, wait_for_blocked_request
from tests.test_roles import participant_headers


def hiring_path(session_id):
    return f"/sessions/{session_id}/acts/1/hiring/upgrade"


@pytest.mark.parametrize("roll, action_points", [(1, 110), (4, 100), (9, 100)])
def test_purchase_preserves_other_resources_and_survives_reconnect_and_start_retry(
    client, engine, prepared_team, monkeypatch, roll, action_points
):
    session_id, members = prepared_team
    random = Mock(return_value=roll - 1)
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    started = client.post(start_path(session_id))
    assert started.status_code == 200
    before = stored_state(engine, session_id)
    expected = {
        **started.json(),
        "action_points": action_points,
        "hiring_level": 1,
        "hiring_capacity": 3,
    }
    headers = participant_headers(members[2])

    bought = client.post(hiring_path(session_id), headers=headers)
    assert bought.status_code == 200
    assert bought.json() == expected
    after = stored_state(engine, session_id)
    assert dict(after) == {
        **before,
        "action_points": action_points,
        "hiring_level": 1,
        "hiring_capacity": 3,
    }
    assert after["hired_development_team_grades"] == ["junior", "junior"]
    assert client.post(hiring_path(session_id), headers=headers).json() == expected

    with TestClient(app) as reconnected:
        snapshot = reconnected.get(snapshot_path(session_id), headers=headers)
        retry = reconnected.post(hiring_path(session_id), headers=headers)
        assert snapshot.status_code == retry.status_code == 200
        assert snapshot.json() == retry.json() == expected
    start_retry = client.post(start_path(session_id))
    assert start_retry.status_code == 200
    assert start_retry.json() == expected
    random.assert_called_once_with(10)


@pytest.mark.parametrize("points", [0, 29, 30])
def test_purchase_action_point_boundary_and_repeat_with_empty_balance(
    client, engine, prepared_team, points
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    with engine.begin() as connection:
        connection.execute(
            ActOneState.__table__.update()
            .where(ActOneState.session_id == UUID(session_id))
            .values(action_points=points)
        )
    before = stored_state(engine, session_id)
    headers = participant_headers(members[2])
    response = client.post(hiring_path(session_id), headers=headers)
    if points < 30:
        assert response.status_code == 409
        assert response.json() == {"detail": "Not enough action points"}
        assert stored_state(engine, session_id) == before
    else:
        assert response.status_code == 200
        assert response.json()["action_points"] == 0
        assert response.json()["hiring_level"] == 1
        assert response.json()["hiring_capacity"] == 3
        retry = client.post(hiring_path(session_id), headers=headers)
        assert retry.status_code == 200
        assert retry.json() == response.json()
        assert stored_state(engine, session_id)["action_points"] == 0


def test_purchase_authenticates_membership_and_role_before_phase(
    client, engine, prepared_team
):
    session_id, members = prepared_team
    spectator = join_session(client, session_id, "Без роли")
    other_session = create_session(client)
    outsider = join_session(client, other_session, "Другая команда")
    path = hiring_path(session_id)
    with TestClient(app) as anonymous:
        denied_attempts = [
            anonymous.post(path),
            anonymous.post(path, headers={"Authorization": "Bearer invalid"}),
            client.post(path),  # Administrator JWT is not a participant credential.
            anonymous.post(path, headers=participant_headers(outsider)),
            anonymous.post(
                hiring_path(str(uuid4())), headers=participant_headers(members[2])
            ),
        ]
    for denied in denied_attempts:
        assert denied.status_code == 401
        assert denied.json() == {"detail": "Invalid credentials"}
        assert denied.headers["WWW-Authenticate"] == "Bearer"
    for member in [members[0], members[1], members[3], spectator]:
        # Member zero is also captain; that does not grant the people role.
        forbidden = client.post(path, headers=participant_headers(member))
        assert forbidden.status_code == 403
        assert forbidden.json() == {"detail": "Required role is not assigned"}
    assert client.post(path, headers=participant_headers(members[2])).status_code == 409
    assert stored_state(engine, session_id) is None

    assert client.post(start_path(session_id)).status_code == 200
    before = stored_state(engine, session_id)
    for member in [members[0], members[1], members[3], spectator]:
        assert client.post(path, headers=participant_headers(member)).status_code == 403
    assert stored_state(engine, session_id) == before


@pytest.mark.parametrize("phase", ["lobby", "preparation", "finished", "missing_state"])
def test_purchase_rejects_inactive_or_missing_state_without_changes(
    client, engine, prepared_team, phase
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    with engine.begin() as connection:
        if phase == "missing_state":
            connection.execute(
                ActOneState.__table__.delete().where(
                    ActOneState.session_id == UUID(session_id)
                )
            )
        else:
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == UUID(session_id))
                .values(status=phase)
            )
    before = stored_state(engine, session_id)
    response = client.post(
        hiring_path(session_id), headers=participant_headers(members[2])
    )
    assert response.status_code == 409
    assert stored_state(engine, session_id) == before


@pytest.mark.parametrize("body", ["absent", "object", "null"])
def test_purchase_accepts_empty_command_forms(client, prepared_team, body):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    kwargs = {"headers": participant_headers(members[2])}
    if body == "object":
        kwargs["json"] = {}
    elif body == "null":
        kwargs["content"] = "null"
        kwargs["headers"]["Content-Type"] = "application/json"
    assert client.post(hiring_path(session_id), **kwargs).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"cost": 0},
        {"action_points": 999},
        {"hiring_level": 2},
        {"hiring_capacity": 10},
        {"participant_id": str(uuid4())},
        [],
        "buy",
    ],
)
def test_purchase_forbids_client_fields(client, engine, prepared_team, payload):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    before = stored_state(engine, session_id)
    response = client.post(
        hiring_path(session_id), headers=participant_headers(members[2]), json=payload
    )
    assert response.status_code == 422
    assert stored_state(engine, session_id) == before


def test_parallel_purchases_wait_for_game_lock_and_charge_once(
    client, engine, prepared_team
):
    session_id, members = prepared_team
    started = client.post(start_path(session_id))
    assert started.status_code == 200
    expected_points = started.json()["action_points"] - 30
    headers = participant_headers(members[2])
    with ThreadPoolExecutor(max_workers=2) as executor:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM game_sessions WHERE id = :id FOR UPDATE"),
                {"id": session_id},
            )
            blocker = connection.scalar(text("SELECT pg_backend_pid()"))
            first = executor.submit(
                client.post, hiring_path(session_id), headers=headers
            )
            first_pid = wait_for_blocked_request(connection, blocker)
            second = executor.submit(
                client.post, hiring_path(session_id), headers=headers
            )
            wait_for_blocked_request(connection, first_pid)
        responses = [request.result(timeout=5) for request in (first, second)]
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["action_points"] == expected_points
    assert stored_state(engine, session_id)["action_points"] == expected_points


def test_purchase_loads_current_state_after_waiting_for_shared_game_lock(
    client, engine, prepared_team
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    with ThreadPoolExecutor(max_workers=1) as executor:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM game_sessions WHERE id = :id FOR UPDATE"),
                {"id": session_id},
            )
            blocker = connection.scalar(text("SELECT pg_backend_pid()"))
            purchase = executor.submit(
                client.post,
                hiring_path(session_id),
                headers=participant_headers(members[2]),
            )
            wait_for_blocked_request(connection, blocker)
            # Change resources while the request waits. A preloaded ORM state
            # would incorrectly spend from the old 130/140 AP snapshot.
            connection.execute(
                ActOneState.__table__.update()
                .where(ActOneState.session_id == UUID(session_id))
                .values(action_points=30)
            )
        response = purchase.result(timeout=5)
    assert response.status_code == 200
    assert response.json()["action_points"] == 0
    assert stored_state(engine, session_id)["action_points"] == 0


@pytest.mark.parametrize("phase", ["write", "commit"])
def test_purchase_write_and_commit_failure_roll_back_all_changes(
    client, engine, prepared_team, phase
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    before = stored_state(engine, session_id)
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION ipo018_reject_hiring() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'private SQL/DSN/traceback sentinel';
            END; $$
        """)
        )
        trigger = (
            "CREATE TRIGGER ipo018_reject BEFORE UPDATE ON act_one_states "
            if phase == "write"
            else "CREATE CONSTRAINT TRIGGER ipo018_reject AFTER UPDATE "
            "ON act_one_states DEFERRABLE INITIALLY DEFERRED "
        )
        connection.execute(
            text(trigger + "FOR EACH ROW EXECUTE FUNCTION ipo018_reject_hiring()")
        )
    try:
        response = client.post(
            hiring_path(session_id), headers=participant_headers(members[2])
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Act one unavailable"}
        assert "sentinel" not in response.text
        assert stored_state(engine, session_id) == before
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER ipo018_reject ON act_one_states"))
            connection.execute(text("DROP FUNCTION ipo018_reject_hiring()"))
    assert (
        client.post(
            hiring_path(session_id), headers=participant_headers(members[2])
        ).status_code
        == 200
    )


def test_purchase_does_not_change_another_session(client, engine, prepared_team):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    other_session = UUID(create_session(client))
    with engine.begin() as connection:
        connection.execute(
            ActOneState.__table__.insert().values(**_valid_state_values(other_session))
        )
    other_before = stored_state(engine, str(other_session))
    assert (
        client.post(
            hiring_path(session_id), headers=participant_headers(members[2])
        ).status_code
        == 200
    )
    assert stored_state(engine, str(other_session)) == other_before
    assert (
        client.post(
            hiring_path(str(other_session)), headers=participant_headers(members[2])
        ).status_code
        == 401
    )
    assert stored_state(engine, str(other_session)) == other_before


def test_missing_credential_does_not_open_database(monkeypatch):
    database = Mock(side_effect=AssertionError("Anonymous purchase accessed DB"))
    monkeypatch.setattr("app.act_one.create_database_engine", database)
    with TestClient(app) as anonymous:
        response = anonymous.post(hiring_path(str(uuid4())))
    assert response.status_code == 401
    database.assert_not_called()
