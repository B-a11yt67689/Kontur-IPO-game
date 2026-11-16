"""Public-HTTP first-act scenarios with independent PostgreSQL verification."""

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, call
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select

from app.database import create_database_engine
from app.main import app
from app.models import ActOneState, GameSession, Participant, RoleAssignment

ROLES = ("cto", "cmo", "people", "cfo")
PNL_INPUTS = {
    "average_price": "1.9",
    "orders_per_client": "1.3",
    "clients": 42,
    "advertising_budget": "0",
    "marketing_payroll": "10",
    "development_payroll": "90",
    "support_payroll": "60",
}
PNL_EXPECTED = {
    "arpc": "2.47",
    "gross_revenue": "103.74",
    "unit_cogs": "1",
    "total_cogs": "54.6",
    "business_costs": "30",
    "operating_costs": "190",
    "result": "-140.86",
    "balance_after_result": "534.14",
}


@dataclass
class Team:
    session_id: str
    actors: dict[str, TestClient]
    members: dict[str, dict[str, str]]

    @property
    def act_path(self):
        return f"/sessions/{self.session_id}/acts/1"


def _assert_no_secrets(payload, credentials):
    secret_fields = {
        "credential",
        "participant_credential",
        "credential_digest",
        "digest",
        "token",
        "access_token",
    }

    def check_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in secret_fields
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    check_keys(payload)
    serialized = json.dumps(payload)
    for credential in credentials:
        assert credential not in serialized
        assert hashlib.sha256(credential.encode()).hexdigest() not in serialized


def _game_payload(response: Response, credentials, status=200):
    assert response.status_code == status, response.text
    payload = response.json()
    _assert_no_secrets(payload, credentials)
    return payload


def _prepare_team(admin: TestClient, resources: ExitStack, name: str) -> Team:
    created = _game_payload(admin.post("/sessions", json={"name": name}), [], 201)
    session_id = created["id"]
    assert created["status"] == "lobby"
    actors, members = {}, {}
    for role in ROLES:
        actor = TestClient(app)
        resources.callback(actor.close)
        joined = actor.post(
            f"/sessions/{session_id}/participants",
            json={"display_name": f"{name} {role}"},
        )
        assert joined.status_code == 201
        member = joined.json()
        actor.headers["Authorization"] = f"Bearer {member['participant_credential']}"
        actors[role], members[role] = actor, member
    credentials = [member["participant_credential"] for member in members.values()]
    preparation_path = f"/sessions/{session_id}/preparation"
    prepared = _game_payload(admin.post(f"{preparation_path}/start"), credentials)
    assert prepared["status"] == "preparation"
    captain = _game_payload(
        admin.put(
            f"{preparation_path}/captain", json={"participant_id": members["cto"]["id"]}
        ),
        credentials,
    )
    assert captain["captain_participant_id"] == members["cto"]["id"]
    for role in ROLES:
        assigned = _game_payload(
            actors[role].put(
                f"{preparation_path}/roles/{role}",
                json={"participant_id": members[role]["id"]},
            ),
            credentials,
        )
        assert assigned == {
            "session_id": session_id,
            "role_code": role,
            "participant_id": members[role]["id"],
        }
    snapshot = _game_payload(actors["cto"].get(preparation_path), credentials)
    assert snapshot["id"] == session_id
    assert snapshot["name"] == name
    assert snapshot["status"] == "preparation"
    assert snapshot["captain_participant_id"] == members["cto"]["id"]
    assert snapshot["roles"] == {role: members[role]["id"] for role in ROLES}
    assert snapshot["participants"] == [
        {"id": member["id"], "display_name": member["display_name"]}
        for member in members.values()
    ]
    return Team(session_id, actors, members)


def _assert_initial_snapshot(snapshot, team, roll, code, points, clients, available):
    assert snapshot["session_id"] == team.session_id
    assert snapshot["act_number"] == 1
    assert snapshot["status"] == "act_one"
    assert datetime.fromisoformat(snapshot["started_at"]).utcoffset() is not None
    assert snapshot["balance"] == "675.00"
    assert snapshot["clients"] == clients
    assert snapshot["action_points"] == points
    assert snapshot["hiring_level"] == 0
    assert snapshot["hiring_capacity"] == 2
    assert snapshot["hired_development_team_grades"] == ["junior", "junior"]
    assert snapshot["experiments_per_development_team"] == 3
    assert snapshot["starting_bonus"]["roll"] == roll
    assert snapshot["starting_bonus"]["code"] == code
    assert snapshot["starting_bonus"]["available"] is available
    assert snapshot["starting_bonus"]["description"]


@pytest.mark.parametrize(
    "roll, code, points, clients, available",
    [
        (1, "action_points", 140, 60, False),
        (9, "clients", 130, 70, False),
        (7, "experiment", 130, 60, True),
    ],
)
def test_first_act_public_api_flow_is_persistent_and_isolates_two_sessions(
    migrated_database, auth_config, monkeypatch, roll, code, points, clients, available
):
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    random = Mock(side_effect=[roll - 1, 3])
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    with ExitStack() as resources:
        # One lifespan owns the process-wide lobby hub. Actor clients have
        # independent headers while sharing this already-running application.
        admin = resources.enter_context(TestClient(app))
        login = admin.post(
            "/auth/login",
            json={
                "username": auth_config["ADMIN_USERNAME"],
                "password": "fictional-test-password",
            },
        )
        assert login.status_code == 200
        admin.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        first = _prepare_team(admin, resources, "Первая команда")
        second = _prepare_team(admin, resources, "Вторая команда")
        assert first.session_id != second.session_id
        teams = (first, second)
        all_members = [member for team in teams for member in team.members.values()]
        credentials = [member["participant_credential"] for member in all_members]
        assert len(set(credentials)) == 8
        assert len({member["id"] for member in all_members}) == 8

        initial_first = _game_payload(
            admin.post(f"{first.act_path}/start"), credentials
        )
        initial_second = _game_payload(
            admin.post(f"{second.act_path}/start"), credentials
        )
        _assert_initial_snapshot(
            initial_first, first, roll, code, points, clients, available
        )
        _assert_initial_snapshot(
            initial_second, second, 4, "orders_per_client", 130, 60, True
        )
        assert random.call_args_list == [call(10), call(10)]
        for team, expected in ((first, initial_first), (second, initial_second)):
            for actor in team.actors.values():
                assert _game_payload(actor.get(team.act_path), credentials) == expected

        for role, actor in first.actors.items():
            if role != "people":
                forbidden = actor.post(f"{first.act_path}/hiring/upgrade")
                _game_payload(forbidden, credentials, 403)
            if role != "cfo":
                forbidden = actor.post(f"{first.act_path}/pnl/preview", json=PNL_INPUTS)
                _game_payload(forbidden, credentials, 403)

        # Cross-session credentials cannot reveal state or mutate either team.
        for target, foreign in ((first, second), (second, first)):
            attempts = [
                foreign.actors["cto"].get(target.act_path),
                foreign.actors["people"].post(f"{target.act_path}/hiring/upgrade"),
                foreign.actors["cfo"].post(
                    f"{target.act_path}/pnl/preview", json=PNL_INPUTS
                ),
            ]
            for denied in attempts:
                assert _game_payload(denied, credentials, 401) == {
                    "detail": "Invalid credentials"
                }
                assert denied.headers["WWW-Authenticate"] == "Bearer"

        bought = _game_payload(
            first.actors["people"].post(f"{first.act_path}/hiring/upgrade"), credentials
        )
        expected_first = {
            **initial_first,
            "action_points": points - 30,
            "hiring_level": 1,
            "hiring_capacity": 3,
        }
        assert bought == expected_first
        assert (
            _game_payload(second.actors["cto"].get(second.act_path), credentials)
            == initial_second
        )

        preview = _game_payload(
            first.actors["cfo"].post(f"{first.act_path}/pnl/preview", json=PNL_INPUTS),
            credentials,
        )
        assert preview == PNL_EXPECTED
        assert (
            _game_payload(first.actors["cfo"].get(first.act_path), credentials)
            == expected_first
        )
        assert expected_first["balance"] == "675.00"
        assert (
            _game_payload(second.actors["cto"].get(second.act_path), credentials)
            == initial_second
        )

        assert (
            _game_payload(admin.post(f"{first.act_path}/start"), credentials)
            == expected_first
        )
        assert (
            _game_payload(admin.post(f"{second.act_path}/start"), credentials)
            == initial_second
        )
        assert (
            _game_payload(
                first.actors["people"].post(f"{first.act_path}/hiring/upgrade"),
                credentials,
            )
            == expected_first
        )

        for role in ("people", "cfo"):
            # Restore identity with the saved credential in a fresh HTTP client.
            refreshed = TestClient(app, headers=dict(first.actors[role].headers))
            resources.callback(refreshed.close)
            identity = _game_payload(
                refreshed.get(f"/sessions/{first.session_id}/participants/me"),
                credentials,
            )
            assert identity["id"] == first.members[role]["id"]
            assert (
                _game_payload(refreshed.get(first.act_path), credentials)
                == expected_first
            )
            if role == "people":
                repeated = _game_payload(
                    refreshed.post(f"{first.act_path}/hiring/upgrade"), credentials
                )
                assert repeated == expected_first
            else:
                repeated = _game_payload(
                    refreshed.post(f"{first.act_path}/pnl/preview", json=PNL_INPUTS),
                    credentials,
                )
                assert repeated == PNL_EXPECTED
        assert (
            _game_payload(second.actors["cfo"].get(second.act_path), credentials)
            == initial_second
        )
        assert random.call_args_list == [call(10), call(10)]

    # Read through a new engine after all HTTP clients close. No test code
    # writes to PostgreSQL; all preparation and economic actions used HTTP.
    identifiers = [UUID(team.session_id) for team in teams]
    engine = create_database_engine(migrated_database)
    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        ActOneState.__table__,
                        GameSession.status,
                        GameSession.captain_participant_id,
                    )
                    .join(GameSession, GameSession.id == ActOneState.session_id)
                    .where(ActOneState.session_id.in_(identifiers))
                )
                .mappings()
                .all()
            )
            persisted_members = connection.execute(
                select(
                    Participant.id,
                    Participant.session_id,
                    Participant.credential_digest,
                ).where(Participant.session_id.in_(identifiers))
            ).all()
            persisted_roles = set(
                connection.execute(
                    select(
                        RoleAssignment.session_id,
                        RoleAssignment.role_code,
                        RoleAssignment.participant_id,
                    ).where(RoleAssignment.session_id.in_(identifiers))
                ).all()
            )
    finally:
        engine.dispose()
    stored = {str(row["session_id"]): row for row in rows}
    assert set(stored) == {first.session_id, second.session_id}
    for team, expected in ((first, expected_first), (second, initial_second)):
        state = stored[team.session_id]
        assert state["status"] == "act_one"
        assert state["captain_participant_id"] == UUID(team.members["cto"]["id"])
        assert state["started_at"] == datetime.fromisoformat(expected["started_at"])
        assert state["balance"] == Decimal("675.00")
        for field in (
            "clients",
            "action_points",
            "hiring_level",
            "hiring_capacity",
            "hired_development_team_grades",
            "experiments_per_development_team",
        ):
            assert state[field] == expected[field]
        assert state["starting_roll"] == expected["starting_bonus"]["roll"]
        assert (
            state["starting_bonus_available"] is expected["starting_bonus"]["available"]
        )
    assert len(persisted_members) == 8
    expected_members = {
        UUID(member["id"]): (UUID(team.session_id), member["participant_credential"])
        for team in teams
        for member in team.members.values()
    }
    for participant_id, session_id, digest in persisted_members:
        expected_session, credential = expected_members[participant_id]
        assert session_id == expected_session
        assert digest == hashlib.sha256(credential.encode()).hexdigest()
    assert persisted_roles == {
        (UUID(team.session_id), role, UUID(team.members[role]["id"]))
        for team in teams
        for role in ROLES
    }
