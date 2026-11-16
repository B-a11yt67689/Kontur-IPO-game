"""First-act start API, transactions and constraints on real PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from alembic import command
from app.database import create_database_engine
from app.main import app
from app.models import ActOneState, Base, GameSession, Participant, RoleAssignment
from tests.test_captain import create_session, join_session, wait_for_blocked_request
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


@pytest.fixture
def engine(migrated_database: str) -> Iterator[Engine]:
    database = create_database_engine(migrated_database)
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def prepared_team(client: TestClient) -> tuple[str, list[dict[str, str]]]:
    session_id = create_session(client)
    members = [
        join_session(client, session_id, role)
        for role in ("cto", "cmo", "people", "cfo")
    ]
    start_preparation(client, session_id)
    assert (
        client.put(
            f"/sessions/{session_id}/preparation/captain",
            json={"participant_id": members[0]["id"]},
        ).status_code
        == 200
    )
    for role, member in zip(("cto", "cmo", "people", "cfo"), members, strict=True):
        assert claim_role(client, session_id, role, member).status_code == 200
    return session_id, members


def start_path(session_id: str) -> str:
    return f"/sessions/{session_id}/acts/1/start"


def stored_state(engine: Engine, session_id: str):
    with engine.connect() as connection:
        return (
            connection.execute(
                select(ActOneState.__table__).where(
                    ActOneState.session_id == UUID(session_id)
                )
            )
            .mappings()
            .one_or_none()
        )


@pytest.mark.parametrize(
    "roll, points, clients, code, available",
    [
        (1, 140, 60, "action_points", False),
        (9, 130, 70, "clients", False),
        (4, 130, 60, "orders_per_client", True),
    ],
)
def test_start_commits_bonus_and_returns_current_snapshot_on_retry(
    client: TestClient,
    engine: Engine,
    prepared_team,
    monkeypatch: pytest.MonkeyPatch,
    roll,
    points,
    clients,
    code,
    available,
) -> None:
    session_id, members = prepared_team
    random = Mock(return_value=roll - 1)
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)

    first = client.post(start_path(session_id))
    assert first.status_code == 200
    snapshot = first.json()
    assert set(snapshot) == {
        "session_id",
        "act_number",
        "status",
        "started_at",
        "balance",
        "clients",
        "action_points",
        "hiring_level",
        "hiring_capacity",
        "hired_development_team_grades",
        "experiments_per_development_team",
        "starting_bonus",
    }
    assert snapshot["session_id"] == session_id
    assert snapshot["act_number"] == 1
    assert snapshot["status"] == "act_one"
    assert datetime.fromisoformat(snapshot["started_at"]).utcoffset() is not None
    assert snapshot["balance"] == "675.00"
    assert snapshot["action_points"] == points
    assert snapshot["clients"] == clients
    assert snapshot["hiring_level"] == 0
    assert snapshot["hiring_capacity"] == 2
    assert snapshot["hired_development_team_grades"] == ["junior", "junior"]
    assert snapshot["experiments_per_development_team"] == 3
    assert snapshot["starting_bonus"]["roll"] == roll
    assert snapshot["starting_bonus"]["code"] == code
    assert snapshot["starting_bonus"]["available"] is available
    assert snapshot["starting_bonus"]["description"]
    assert "credential" not in first.text
    assert "digest" not in first.text
    for member in members:
        assert member["participant_credential"] not in first.text

    # A new connection observes both writes before the HTTP success is returned.
    state = stored_state(engine, session_id)
    assert state["balance"] == Decimal("675.00")
    assert state["action_points"] == points
    assert state["clients"] == clients
    assert state["starting_roll"] == roll
    assert state["starting_bonus_available"] is available
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(GameSession.status).where(GameSession.id == UUID(session_id))
            )
            == "act_one"
        )

    assert client.post(start_path(session_id)).json() == snapshot
    # Simulate the purchase implemented in IPO-018. Retrying start must keep it.
    with engine.begin() as connection:
        connection.execute(
            ActOneState.__table__.update()
            .where(ActOneState.session_id == UUID(session_id))
            .values(hiring_level=1, hiring_capacity=3, action_points=points - 30)
        )
    retry = client.post(start_path(session_id))
    assert retry.status_code == 200
    assert retry.json() == {
        **snapshot,
        "hiring_level": 1,
        "hiring_capacity": 3,
        "action_points": points - 30,
    }
    random.assert_called_once_with(10)


@pytest.mark.parametrize("body", ["absent", "object", "null"])
def test_start_accepts_only_empty_command_forms(client, prepared_team, body):
    session_id, _ = prepared_team
    kwargs = {}
    if body == "object":
        kwargs["json"] = {}
    elif body == "null":
        kwargs["content"] = "null"
        kwargs["headers"] = {"Content-Type": "application/json"}
    assert client.post(start_path(session_id), **kwargs).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"roll": 1},
        {"balance": "999"},
        {"action_points": 500},
        {"clients": 70},
        {"starting_bonus": {}},
        {"status": "act_one"},
        [],
        1,
        "start",
    ],
)
def test_start_rejects_client_owned_fields_and_non_objects(
    client, engine, prepared_team, monkeypatch, payload
):
    session_id, _ = prepared_team
    random = Mock(side_effect=AssertionError("RNG must not run"))
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    assert client.post(start_path(session_id), json=payload).status_code == 422
    assert stored_state(engine, session_id) is None
    random.assert_not_called()


def test_start_requires_admin_before_database(
    monkeypatch: pytest.MonkeyPatch, admin_headers: dict[str, str]
) -> None:
    engine = Mock(side_effect=AssertionError("Unauthorized request accessed DB"))
    monkeypatch.setattr("app.act_one.create_database_engine", engine)
    path = start_path(str(uuid4()))
    with TestClient(app) as anonymous:
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            denied = anonymous.post(path, headers=headers)
            assert denied.status_code == 401
            assert denied.headers["WWW-Authenticate"] == "Bearer"
    engine.assert_not_called()


def test_participant_bearer_cannot_start_act(client, prepared_team):
    session_id, members = prepared_team
    assert (
        client.post(
            start_path(session_id), headers=participant_headers(members[0])
        ).status_code
        == 401
    )


@pytest.mark.parametrize("missing", ["captain", "cto", "cmo", "people", "cfo"])
def test_start_requires_captain_and_all_roles(
    client, engine, prepared_team, monkeypatch, missing
):
    session_id, _ = prepared_team
    with engine.begin() as connection:
        if missing == "captain":
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == UUID(session_id))
                .values(captain_participant_id=None)
            )
        else:
            connection.execute(
                RoleAssignment.__table__.delete().where(
                    RoleAssignment.session_id == UUID(session_id),
                    RoleAssignment.role_code == missing,
                )
            )
    random = Mock(side_effect=AssertionError("Not-ready session rolled"))
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    assert client.post(start_path(session_id)).status_code == 409
    assert stored_state(engine, session_id) is None
    random.assert_not_called()


@pytest.mark.parametrize("status", ["lobby", "act_two", "finished", "act_one"])
def test_wrong_phase_and_missing_act_state_do_not_initialize(
    client, engine, prepared_team, monkeypatch, status
):
    session_id, _ = prepared_team
    with engine.begin() as connection:
        connection.execute(
            GameSession.__table__.update()
            .where(GameSession.id == UUID(session_id))
            .values(status=status)
        )
    random = Mock(side_effect=AssertionError("Wrong phase rolled"))
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    response = client.post(start_path(session_id))
    assert response.status_code == 409
    assert stored_state(engine, session_id) is None
    random.assert_not_called()


def test_start_unknown_session_returns_404(client):
    response = client.post(start_path(str(uuid4())))
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_parallel_starts_wait_for_session_lock_and_roll_once(
    client, engine, prepared_team, monkeypatch
):
    session_id, _ = prepared_team
    random = Mock(return_value=0)
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    with ThreadPoolExecutor(max_workers=2) as executor:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM game_sessions WHERE id = :id FOR UPDATE"),
                {"id": session_id},
            )
            blocker = connection.scalar(text("SELECT pg_backend_pid()"))
            first = executor.submit(client.post, start_path(session_id))
            first_pid = wait_for_blocked_request(connection, blocker)
            second = executor.submit(client.post, start_path(session_id))
            wait_for_blocked_request(connection, first_pid)
            random.assert_not_called()
        responses = [request.result(timeout=5) for request in (first, second)]
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert stored_state(engine, session_id)["action_points"] == 140
    with engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(ActOneState.session_id).where(
                        ActOneState.session_id == UUID(session_id)
                    )
                ).all()
            )
            == 1
        )
    random.assert_called_once_with(10)


@pytest.mark.parametrize("phase", ["write", "commit"])
def test_start_database_errors_roll_back_both_state_and_phase(
    client, engine, prepared_team, phase
):
    session_id, _ = prepared_team
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION ipo016_reject_start() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'private SQL/DSN/traceback sentinel';
            END; $$
        """)
        )
        trigger = (
            "CREATE TRIGGER ipo016_reject BEFORE INSERT ON act_one_states "
            if phase == "write"
            else "CREATE CONSTRAINT TRIGGER ipo016_reject AFTER INSERT "
            "ON act_one_states DEFERRABLE INITIALLY DEFERRED "
        )
        connection.execute(
            text(trigger + "FOR EACH ROW EXECUTE FUNCTION ipo016_reject_start()")
        )
    try:
        response = client.post(start_path(session_id))
        assert response.status_code == 503
        assert response.json() == {"detail": "Act one unavailable"}
        assert "sentinel" not in response.text
        assert stored_state(engine, session_id) is None
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(GameSession.status).where(GameSession.id == UUID(session_id))
                )
                == "preparation"
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER ipo016_reject ON act_one_states"))
            connection.execute(text("DROP FUNCTION ipo016_reject_start()"))
    assert client.post(start_path(session_id)).status_code == 200


@pytest.mark.parametrize("failure", ["configuration", "connection"])
def test_start_connection_errors_are_sanitized(monkeypatch, admin_headers, failure):
    error = (
        ValueError("private configuration sentinel")
        if failure == "configuration"
        else OperationalError("private SQL sentinel", {}, Exception("private DSN"))
    )
    monkeypatch.setattr("app.act_one.create_database_engine", Mock(side_effect=error))
    with TestClient(app, headers=admin_headers) as client:
        response = client.post(start_path(str(uuid4())))
    assert response.status_code == 503
    assert response.json() == {"detail": "Act one unavailable"}


def test_start_and_retry_publish_committed_lobby(client, prepared_team):
    session_id, members = prepared_team
    with client.websocket_connect(
        f"/sessions/{session_id}/lobby/ws",
        headers=participant_headers(members[0]),
    ) as websocket:
        initial = websocket.receive_json()
        assert initial["status"] == "preparation"
        response = client.post(start_path(session_id))
        assert response.status_code == 200
        assert websocket.receive_json() == {**initial, "status": "act_one"}
        assert client.post(start_path(session_id)).json() == response.json()
        assert websocket.receive_json() == {**initial, "status": "act_one"}


def test_first_start_succeeds_when_publication_fails(
    client, engine, prepared_team, monkeypatch
):
    session_id, _ = prepared_team
    publish = app.state.lobby_hub.publish

    async def failed_publish(requested_session_id):
        assert str(requested_session_id) == session_id
        assert stored_state(engine, session_id) is not None
        raise RuntimeError("private publication sentinel")

    monkeypatch.setattr(app.state.lobby_hub, "publish", failed_publish)
    first = client.post(start_path(session_id))
    assert first.status_code == 200
    assert stored_state(engine, session_id) is not None
    monkeypatch.setattr(app.state.lobby_hub, "publish", publish)
    retry = client.post(start_path(session_id))
    assert retry.status_code == 200
    assert retry.json() == first.json()


def _valid_state_values(session_id):
    return {
        "session_id": session_id,
        "balance": Decimal("675.00"),
        "clients": 60,
        "action_points": 130,
        "hiring_level": 0,
        "hiring_capacity": 2,
        "hired_development_team_grades": ["junior", "junior"],
        "experiments_per_development_team": 3,
        "starting_roll": 4,
        "starting_bonus_available": True,
    }


@pytest.mark.parametrize(
    "changes, constraint",
    [
        ({"clients": -1}, "ck_act_one_states_clients"),
        ({"action_points": -1}, "ck_act_one_states_action_points"),
        ({"experiments_per_development_team": 0}, "ck_act_one_states_experiments"),
        ({"starting_roll": 0}, "ck_act_one_states_starting_roll"),
        ({"starting_roll": 11}, "ck_act_one_states_starting_roll"),
        ({"hiring_level": 2, "hiring_capacity": 3}, None),
        ({"hiring_level": -1}, None),
        ({"hiring_capacity": 3}, "ck_act_one_states_hiring_capacity"),
        ({"hiring_level": 1}, "ck_act_one_states_hiring_capacity"),
        ({"hired_development_team_grades": {}}, "ck_act_one_states_team_grades_array"),
        (
            {"hired_development_team_grades": None},
            "ck_act_one_states_team_grades_array",
        ),
        ({"session_id": uuid4()}, "act_one_states_session_id_fkey"),
    ],
)
def test_act_state_database_constraints(engine, client, changes, constraint):
    session_id = UUID(create_session(client))
    values = {**_valid_state_values(session_id), **changes}
    with pytest.raises(IntegrityError) as rejected, engine.begin() as connection:
        connection.execute(ActOneState.__table__.insert().values(**values))
    if constraint:
        assert rejected.value.orig.diag.constraint_name == constraint


def test_act_state_schema_primary_key_and_session_delete_cascade(client, engine):
    session_id = UUID(create_session(client))
    values = _valid_state_values(session_id)
    with engine.begin() as connection:
        inspector = inspect(connection)
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )
        assert inspector.get_pk_constraint("act_one_states")["constrained_columns"] == [
            "session_id"
        ]
        columns = {
            column["name"]: column for column in inspector.get_columns("act_one_states")
        }
        assert all(not column["nullable"] for column in columns.values())
        assert columns["started_at"]["type"].timezone
        foreign = inspector.get_foreign_keys("act_one_states")[0]
        assert foreign["referred_table"] == "game_sessions"
        assert foreign["options"]["ondelete"] == "CASCADE"
        connection.execute(ActOneState.__table__.insert().values(**values))
    with pytest.raises(IntegrityError) as rejected, engine.begin() as connection:
        connection.execute(ActOneState.__table__.insert().values(**values))
    assert rejected.value.orig.diag.constraint_name == "act_one_states_pkey"
    with engine.begin() as connection:
        connection.execute(
            GameSession.__table__.delete().where(GameSession.id == session_id)
        )
    assert stored_state(engine, str(session_id)) is None


def test_act_state_migration_preserves_preparation_data(migrated_database, engine):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    session_id, participant_id = uuid4(), uuid4()
    command.downgrade(config, "0005_role_assignments")
    try:
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.insert().values(
                    id=session_id, name="legacy team", status="preparation"
                )
            )
            connection.execute(
                Participant.__table__.insert().values(
                    id=participant_id,
                    session_id=session_id,
                    display_name="legacy captain",
                    credential_digest="a" * 64,
                )
            )
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == session_id)
                .values(captain_participant_id=participant_id)
            )
            connection.execute(
                RoleAssignment.__table__.insert().values(
                    session_id=session_id,
                    role_code="cto",
                    participant_id=participant_id,
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(GameSession.captain_participant_id).where(
                        GameSession.id == session_id
                    )
                )
                == participant_id
            )
            assert (
                connection.scalar(
                    select(RoleAssignment.participant_id).where(
                        RoleAssignment.session_id == session_id
                    )
                )
                == participant_id
            )
            assert (
                connection.scalar(
                    select(Participant.display_name).where(
                        Participant.id == participant_id
                    )
                )
                == "legacy captain"
            )
            assert connection.execute(select(ActOneState.__table__)).all() == []
        command.downgrade(config, "0005_role_assignments")
        with engine.connect() as connection:
            assert not inspect(connection).has_table("act_one_states")
            assert connection.execute(
                select(
                    GameSession.name,
                    GameSession.status,
                    GameSession.captain_participant_id,
                ).where(GameSession.id == session_id)
            ).one() == ("legacy team", "preparation", participant_id)
            assert (
                connection.scalar(
                    select(RoleAssignment.participant_id).where(
                        RoleAssignment.session_id == session_id
                    )
                )
                == participant_id
            )
    finally:
        command.upgrade(config, "head")
