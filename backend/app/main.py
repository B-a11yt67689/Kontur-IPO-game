"""Minimal HTTP service for the IPO learning game."""

import hashlib
import secrets
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import login_token, require_admin
from app.database import create_database_engine, transaction
from app.models import GameSession, Participant
from app.schemas import (
    LobbyParticipant,
    LobbySnapshot,
    LoginRequest,
    LoginResponse,
    ParticipantIdentity,
    ParticipantJoin,
    ParticipantJoined,
    SessionCreate,
    SessionCreated,
)

app = FastAPI()
participant_bearer = HTTPBearer(auto_error=False)


@app.exception_handler(RequestValidationError)
async def safe_validation(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Never reflect credentials, even in malformed JSON or missing-field errors."""
    if request.url.path == "/auth/login":
        return JSONResponse(
            status_code=422, content={"detail": "Invalid login request"}
        )
    return await request_validation_exception_handler(request, exc)


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    return LoginResponse(
        access_token=login_token(payload.username, payload.password.get_secret_value())
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the HTTP process is responding."""
    return {"status": "ok"}


@app.post(
    "/sessions",
    status_code=201,
    response_model=SessionCreated,
    dependencies=[Depends(require_admin)],
)
def create_session(payload: SessionCreate) -> SessionCreated:
    """Create a session as the administrator; return only after commit."""
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


@app.post(
    "/sessions/{session_id}/participants",
    status_code=201,
    response_model=ParticipantJoined,
)
def join_session(session_id: UUID, payload: ParticipantJoin) -> ParticipantJoined:
    """Create distinct membership and issue a one-time displayable credential."""
    credential = secrets.token_urlsafe(32)
    credential_digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                if session.get(GameSession, session_id) is None:
                    raise HTTPException(status_code=404, detail="Session not found")
                participant = Participant(
                    session_id=session_id,
                    display_name=payload.display_name,
                    credential_digest=credential_digest,
                )
                session.add(participant)
                session.flush()
                response = ParticipantJoined(
                    id=participant.id,
                    display_name=participant.display_name,
                    participant_credential=credential,
                )
            return response
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503, detail="Participant join unavailable"
        ) from None


@app.get(
    "/sessions/{session_id}/participants/me",
    response_model=ParticipantIdentity,
)
def get_my_participant(
    session_id: UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(participant_bearer),
) -> ParticipantIdentity:
    """Restore membership only when its session-scoped credential is supplied."""
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid participant credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    credential_digest = hashlib.sha256(
        credentials.credentials.encode("utf-8")
    ).hexdigest()
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                participant = session.scalar(
                    select(Participant).where(
                        Participant.session_id == session_id,
                        Participant.credential_digest == credential_digest,
                    )
                )
                if participant is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid participant credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                response = ParticipantIdentity.model_validate(participant)
            return response
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503, detail="Participant lookup unavailable"
        ) from None


@app.get("/sessions/{session_id}/lobby", response_model=LobbySnapshot)
def get_lobby(
    session_id: UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(participant_bearer),
) -> LobbySnapshot:
    """Return an authenticated snapshot of the session's current lobby state."""
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid participant credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    credential_digest = hashlib.sha256(
        credentials.credentials.encode("utf-8")
    ).hexdigest()
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                game = session.get(GameSession, session_id)
                if game is None:
                    raise HTTPException(status_code=404, detail="Session not found")
                participant = session.scalar(
                    select(Participant.id).where(
                        Participant.session_id == session_id,
                        Participant.credential_digest == credential_digest,
                    )
                )
                if participant is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid participant credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                participants = session.scalars(
                    select(Participant)
                    .where(Participant.session_id == session_id)
                    .order_by(Participant.created_at, Participant.id)
                ).all()
                response = LobbySnapshot(
                    id=game.id,
                    name=game.name,
                    status=game.status,
                    participants=[
                        LobbyParticipant.model_validate(item) for item in participants
                    ],
                )
            return response
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503, detail="Lobby lookup unavailable"
        ) from None
