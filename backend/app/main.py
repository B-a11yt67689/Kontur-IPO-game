"""Minimal HTTP service for the IPO learning game."""

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.database import create_database_engine, transaction
from app.models import GameSession
from app.schemas import SessionCreate, SessionCreated

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the HTTP process is responding."""
    return {"status": "ok"}


@app.post("/sessions", status_code=201, response_model=SessionCreated)
def create_session(payload: SessionCreate) -> SessionCreated:
    """Create a session for local development; return only after commit."""
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                game = GameSession(name=payload.name)
                session.add(game)
                session.flush()
                # Snapshot defaults before commit expires the ORM object.
                response = SessionCreated.model_validate(game)
            return response
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503, detail="Session creation unavailable"
        ) from None
