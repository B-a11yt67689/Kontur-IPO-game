"""Real PostgreSQL tests; require an empty, explicitly named *_test database."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.database import create_database_engine, database_url

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
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
