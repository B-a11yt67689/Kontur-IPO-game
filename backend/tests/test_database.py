"""Real PostgreSQL persistence and schema tests."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.database import create_database_engine, transaction
from app.models import GameSession


def test_migration_schema(migrated_database: str) -> None:
    engine = create_database_engine(migrated_database)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {
                column["name"]: column
                for column in inspector.get_columns("game_sessions")
            }
            assert set(columns) == {"id", "name", "status", "created_at"}
            assert all(not column["nullable"] for column in columns.values())
            assert columns["created_at"]["type"].timezone
            assert inspector.get_pk_constraint("game_sessions")[
                "constrained_columns"
            ] == ["id"]
            assert not inspector.get_unique_constraints("game_sessions")
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("0002_session_name_length")
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
            expected = (first.id, first.name, first.status, first.created_at)
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
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

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
