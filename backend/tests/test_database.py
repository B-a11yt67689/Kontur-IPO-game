"""Real PostgreSQL tests; require an empty, explicitly named *_test database."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database import create_database_engine, database_url, transaction
from app.models import GameSession

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def migrated_database() -> Iterator[str]:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Set TEST_DATABASE_URL to an empty dedicated *_test PostgreSQL DB")
    url = database_url(value)
    if not url.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end with _test")
    development = os.environ.get("DATABASE_URL")
    if development:
        dev_url = database_url(development)
        if (url.host, url.port or 5432, url.database) == (
            dev_url.host,
            dev_url.port or 5432,
            dev_url.database,
        ):
            pytest.fail("Test and development databases must be different")
    engine = create_database_engine(value)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != "public":
                pytest.fail("Test DB must use the public schema")
            inspector = inspect(connection)
            if inspector.get_table_names(schema="public") or inspector.get_view_names(
                schema="public"
            ):
                pytest.fail("Test DB public schema must be empty; nothing was changed")
    finally:
        engine.dispose()

    config = Config(str(BACKEND / "alembic.ini"))
    # Use the same Alembic environment as the CLI, without logging the URL.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", value)
        command.upgrade(config, "head")
        try:
            command.upgrade(config, "head")
            yield value
        finally:
            command.downgrade(config, "base")
            # Alembic retains its empty version table on downgrade.
            engine = create_database_engine(value)
            try:
                with engine.begin() as connection:
                    connection.execute(text("DROP TABLE alembic_version"))
            finally:
                engine.dispose()


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
            ) == ("0001_game_sessions")
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
