"""Minimal HTTP service for the IPO learning game."""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.auth import login_token, require_admin
from app.database import create_database_engine, transaction
from app.models import GameSession
from app.schemas import LoginRequest, LoginResponse, SessionCreate, SessionCreated

app = FastAPI()


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
