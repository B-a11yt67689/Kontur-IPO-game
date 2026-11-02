"""Real PostgreSQL tests; require an empty, explicitly named *_test database."""

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from alembic import command
from app.database import create_database_engine, database_url
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def admin_hash() -> str:
    return PasswordHasher().hash("fictional-test-password")


@pytest.fixture
def auth_config(monkeypatch: pytest.MonkeyPatch, admin_hash: str) -> dict[str, str]:
    settings = {
        "ADMIN_USERNAME": "fictional-test-admin",
        "ADMIN_PASSWORD_HASH": admin_hash,
        "JWT_SIGNING_KEY": secrets.token_urlsafe(48),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    return settings


@pytest.fixture
def admin_headers(auth_config: dict[str, str]) -> dict[str, str]:
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": auth_config["ADMIN_USERNAME"],
                "password": "fictional-test-password",
            },
        )
        assert response.status_code == 200
        return {"Authorization": "Bearer " + response.json()["access_token"]}


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
