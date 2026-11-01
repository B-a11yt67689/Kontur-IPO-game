"""Explicit database configuration and transaction lifetime; no startup I/O."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session


def database_url(value: str | None = None) -> URL:
    """Read a PostgreSQL psycopg URL only when database access is requested."""
    value = value if value is not None else os.environ.get("DATABASE_URL")
    if not value:
        raise ValueError("Set DATABASE_URL before accessing the database")
    try:
        url = make_url(value)
    except ArgumentError:
        raise ValueError("Invalid database URL") from None
    if url.drivername != "postgresql+psycopg" or not url.database:
        raise ValueError("Use postgresql+psycopg and an explicit database name")
    return url


def create_database_engine(value: str | None = None) -> Engine:
    """Create a lazy engine; its owner must dispose it when finished."""
    return create_engine(database_url(value), hide_parameters=True)


@contextmanager
def transaction(engine: Engine) -> Iterator[Session]:
    """Commit successful work, roll back errors, and always close the session."""
    with Session(engine) as session, session.begin():
        yield session
