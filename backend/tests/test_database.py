"""Real PostgreSQL persistence and schema tests."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database import create_database_engine, transaction
from app.models import GameSession, Participant, RoleAssignment


def test_migration_schema(migrated_database: str) -> None:
    engine = create_database_engine(migrated_database)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {
                column["name"]: column
                for column in inspector.get_columns("game_sessions")
            }
            assert set(columns) == {
                "id",
                "name",
                "status",
                "created_at",
                "captain_participant_id",
            }
            assert {name for name, column in columns.items() if column["nullable"]} == {
                "captain_participant_id"
            }
            assert columns["created_at"]["type"].timezone
            assert inspector.get_pk_constraint("game_sessions")[
                "constrained_columns"
            ] == ["id"]
            assert not inspector.get_unique_constraints("game_sessions")
            participant_columns = {
                column["name"]: column
                for column in inspector.get_columns("participants")
            }
            assert set(participant_columns) == {
                "id",
                "session_id",
                "display_name",
                "credential_digest",
                "created_at",
            }
            assert all(
                not column["nullable"] for column in participant_columns.values()
            )
            assert participant_columns["credential_digest"]["type"].length == 64
            assert (
                inspector.get_foreign_keys("participants")[0]["referred_table"]
                == "game_sessions"
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_role_assignments"
            )
    finally:
        engine.dispose()


def test_committed_session_survives_new_connection(migrated_database: str) -> None:
    writer = create_database_engine(migrated_database)
    try:
        with transaction(writer) as session:
            first = GameSession(name="Команда IPO")
            second = GameSession(name="Команда IPO")
            session.add_all([first, second])
            session.flush()
            expected = (first.id, first.name, first.status, first.created_at, None)
            assert isinstance(first.id, UUID)
            assert first.id != second.id
            assert first.status == "lobby"
            assert first.created_at.utcoffset() is not None
    finally:
        writer.dispose()

    reader = create_database_engine(migrated_database)
    try:
        with reader.connect() as connection:
            actual = connection.execute(
                select(GameSession.__table__).where(GameSession.id == expected[0])
            ).one()
            assert tuple(actual) == expected
    finally:
        reader.dispose()


@pytest.mark.parametrize("column", ["id", "name", "status", "created_at"])
def test_database_rejects_null(migrated_database: str, column: str) -> None:
    engine = create_database_engine(migrated_database)
    values = dict(id=uuid4(), name="required", status="lobby")
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            if column == "created_at":
                connection.execute(
                    text(
                        "INSERT INTO game_sessions (name, created_at) "
                        "VALUES ('x', NULL)"
                    )
                )
            else:
                values[column] = None
                connection.execute(GameSession.__table__.insert().values(**values))
    finally:
        engine.dispose()


def test_database_rejects_duplicate_uuid(migrated_database: str) -> None:
    engine = create_database_engine(migrated_database)
    identifier = uuid4()
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            for _ in range(2):
                connection.execute(
                    GameSession.__table__.insert().values(id=identifier, name="same id")
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["database", "application"])
def test_failed_transaction_leaves_no_partial_data(
    migrated_database: str, failure: str
) -> None:
    engine = create_database_engine(migrated_database)
    identifier = uuid4()
    error = IntegrityError if failure == "database" else RuntimeError
    try:
        with pytest.raises(error), transaction(engine) as session:
            session.add(GameSession(id=identifier, name="must roll back"))
            session.flush()  # The first write really reached PostgreSQL.
            if failure == "application":
                raise RuntimeError("operation failed")
            session.execute(text("INSERT INTO game_sessions (name) VALUES (NULL)"))
        with engine.connect() as connection:
            assert (
                connection.execute(
                    select(GameSession.id).where(GameSession.id == identifier)
                ).first()
                is None
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", ["ю" * 255, "🦊" * 255])
def test_database_accepts_name_boundary(migrated_database: str, name: str) -> None:
    engine = create_database_engine(migrated_database)
    try:
        with transaction(engine) as session:
            session.add(GameSession(name=name))
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", ["я" * 256, "🚀" * 256])
def test_database_rejects_long_name(migrated_database: str, name: str) -> None:
    engine = create_database_engine(migrated_database)
    try:
        with pytest.raises(IntegrityError), transaction(engine) as session:
            session.add(GameSession(name=name))
    finally:
        engine.dispose()


def test_length_migration_preserves_existing_long_name(migrated_database: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_database_engine(migrated_database)
    identifier = uuid4()
    name = "legacy" * 50
    command.downgrade(config, "0001_game_sessions")
    try:
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.insert().values(id=identifier, name=name)
            )
        with pytest.raises(IntegrityError):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(GameSession.name).where(GameSession.id == identifier)
                )
                == name
            )
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("0001_game_sessions")
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    GameSession.__table__.delete().where(GameSession.id == identifier)
                )
            command.upgrade(config, "head")
        finally:
            engine.dispose()


def test_captain_migration_preserves_legacy_session_and_participant(
    migrated_database: str,
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_database_engine(migrated_database)
    session_id, participant_id = uuid4(), uuid4()
    try:
        command.downgrade(config, "0003_participants")
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.insert().values(
                    id=session_id, name="legacy", status="preparation"
                )
            )
            connection.execute(
                Participant.__table__.insert().values(
                    id=participant_id,
                    session_id=session_id,
                    display_name="legacy participant",
                    credential_digest="a" * 64,
                )
            )
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(
                select(
                    GameSession.name,
                    GameSession.status,
                    GameSession.captain_participant_id,
                ).where(GameSession.id == session_id)
            ).one() == ("legacy", "preparation", None)
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == session_id)
                .values(captain_participant_id=participant_id)
            )

        command.downgrade(config, "0003_participants")
        with engine.connect() as connection:
            columns = inspect(connection).get_columns("game_sessions")
            assert "captain_participant_id" not in {
                column["name"] for column in columns
            }
            assert connection.execute(
                select(GameSession.name, GameSession.status).where(
                    GameSession.id == session_id
                )
            ).one() == ("legacy", "preparation")
            assert connection.execute(
                select(Participant.session_id, Participant.display_name).where(
                    Participant.id == participant_id
                )
            ).one() == (session_id, "legacy participant")
    finally:
        try:
            command.upgrade(config, "head")
        finally:
            engine.dispose()


def test_database_rejects_cross_session_captain_and_preserves_session_cascade(
    migrated_database: str,
) -> None:
    engine = create_database_engine(migrated_database)
    session_id, other_session_id, participant_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.insert(),
                [
                    {"id": session_id, "name": "owner"},
                    {"id": other_session_id, "name": "other"},
                ],
            )
            connection.execute(
                Participant.__table__.insert().values(
                    id=participant_id,
                    session_id=session_id,
                    display_name="captain",
                    credential_digest="a" * 64,
                )
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == other_session_id)
                .values(captain_participant_id=participant_id)
            )

        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == session_id)
                .values(captain_participant_id=participant_id)
            )
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.delete().where(GameSession.id == session_id)
            )
            assert (
                connection.scalar(
                    select(Participant.id).where(Participant.id == participant_id)
                )
                is None
            )
    finally:
        engine.dispose()


def test_role_migration_preserves_legacy_data_and_enforces_claim_constraints(
    migrated_database: str,
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_database_engine(migrated_database)
    session_id, other_session_id = uuid4(), uuid4()
    captain_id, teammate_id = uuid4(), uuid4()
    assignments = RoleAssignment.__table__
    try:
        command.downgrade(config, "0004_session_captain")
        with engine.begin() as connection:
            connection.execute(
                GameSession.__table__.insert(),
                [
                    {"id": session_id, "name": "legacy team", "status": "preparation"},
                    {"id": other_session_id, "name": "other team", "status": "lobby"},
                ],
            )
            connection.execute(
                Participant.__table__.insert(),
                [
                    {
                        "id": participant_id,
                        "session_id": session_id,
                        "display_name": display_name,
                        "credential_digest": digest * 64,
                    }
                    for participant_id, display_name, digest in (
                        (captain_id, "Captain", "a"),
                        (teammate_id, "Teammate", "b"),
                    )
                ],
            )
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == session_id)
                .values(captain_participant_id=captain_id)
            )

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(select(assignments)).all() == []
            assert (
                connection.scalar(
                    select(GameSession.captain_participant_id).where(
                        GameSession.id == session_id
                    )
                )
                == captain_id
            )
            connection.execute(
                assignments.insert().values(
                    session_id=session_id, role_code="cto", participant_id=captain_id
                )
            )

        for claim_session, code, participant, constraint in (
            (session_id, "ceo", teammate_id, "ck_role_assignments_role_code"),
            (session_id, "cto", teammate_id, "role_assignments_pkey"),
            (session_id, "cfo", captain_id, "uq_role_assignments_participant"),
            (other_session_id, "cfo", teammate_id, "fk_role_assignments_participant"),
        ):
            with (
                pytest.raises(IntegrityError) as rejected,
                engine.begin() as connection,
            ):
                connection.execute(
                    assignments.insert().values(
                        session_id=claim_session,
                        role_code=code,
                        participant_id=participant,
                    )
                )
            assert rejected.value.orig.diag.constraint_name == constraint

        command.downgrade(config, "0004_session_captain")
        with engine.connect() as connection:
            assert not inspect(connection).has_table("role_assignments")
            assert connection.execute(
                select(GameSession.status, GameSession.captain_participant_id).where(
                    GameSession.id == session_id
                )
            ).one() == ("preparation", captain_id)
            assert set(
                connection.scalars(
                    select(Participant.id).where(Participant.session_id == session_id)
                )
            ) == {captain_id, teammate_id}
    finally:
        try:
            command.upgrade(config, "head")
        finally:
            engine.dispose()
