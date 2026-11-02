"""Minimal HTTP service for the IPO learning game."""

import asyncio
import hashlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID
from weakref import WeakValueDictionary

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

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


def _get_lobby_snapshot(
    session_id: UUID, credential: str | None = None
) -> LobbySnapshot:
    credential_digest = (
        hashlib.sha256(credential.encode("utf-8")).hexdigest()
        if credential is not None
        else None
    )
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                game = session.get(GameSession, session_id)
                if game is None:
                    raise HTTPException(status_code=404, detail="Session not found")
                if credential_digest is not None:
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


@dataclass
class LobbyConnection:
    websocket: WebSocket
    handler_task: asyncio.Task | None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    last_revision: int = -1
    active: bool = True


@dataclass
class SessionConnections:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    snapshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    revision: int = 0
    connections: dict[WebSocket, LobbyConnection] = field(default_factory=dict)


class LobbyHub:
    """Keep connected sockets current with committed lobby snapshots."""

    send_timeout = 1.0
    close_timeout = 1.0

    def __init__(self) -> None:
        self._registry_lock = asyncio.Lock()
        self._sessions: WeakValueDictionary[UUID, SessionConnections] = (
            WeakValueDictionary()
        )

    async def _session(
        self, session_id: UUID, *, create: bool
    ) -> SessionConnections | None:
        async with self._registry_lock:
            connections = self._sessions.get(session_id)
            if connections is None and create:
                connections = SessionConnections()
                self._sessions[session_id] = connections
            return connections

    async def _send(
        self, connection: LobbyConnection, payload: dict, revision: int
    ) -> bool:
        try:
            async with asyncio.timeout(self.send_timeout):
                await connection.ready.wait()
                async with connection.send_lock:
                    if not connection.active or revision <= connection.last_revision:
                        return True
                    await connection.websocket.send_json(payload)
                    connection.last_revision = revision
        except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
            return False
        return True

    async def _snapshot(
        self, session_id: UUID, connections: SessionConnections
    ) -> tuple[LobbySnapshot, int]:
        async with connections.snapshot_lock:
            snapshot = await run_in_threadpool(_get_lobby_snapshot, session_id)
            connections.revision += 1
            return snapshot, connections.revision

    async def _remove_if_current(
        self, connections: SessionConnections, connection: LobbyConnection
    ) -> bool:
        async with connections.lock:
            if connections.connections.get(connection.websocket) is not connection:
                return False
            connections.connections.pop(connection.websocket)
            connection.active = False
            return True

    async def _cancel_handler(self, connection: LobbyConnection) -> None:
        current_task = asyncio.current_task()
        if (
            connection.handler_task is not None
            and connection.handler_task is not current_task
        ):
            connection.handler_task.cancel()

    async def _initial_send(
        self, connection: LobbyConnection, payload: dict, revision: int
    ) -> bool:
        try:
            async with connection.send_lock:
                async with asyncio.timeout(self.send_timeout):
                    await connection.websocket.send_json(payload)
                    connection.last_revision = revision
        except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
            return False
        finally:
            connection.ready.set()
        return True

    async def _close(
        self,
        websocket: WebSocket,
        *,
        code: int = 1011,
        reason: str = "Lobby send timed out",
    ) -> None:
        try:
            await asyncio.wait_for(
                websocket.close(code=code, reason=reason),
                timeout=self.close_timeout,
            )
        except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
            pass

    async def connect(
        self,
        websocket: WebSocket,
        session_id: UUID,
        handler_task: asyncio.Task | None = None,
    ) -> tuple[SessionConnections, LobbyConnection]:
        connections = await self._session(session_id, create=True)
        assert connections is not None
        connection = LobbyConnection(websocket, handler_task)
        await websocket.accept()
        async with connections.lock:
            connections.connections[websocket] = connection
        try:
            snapshot, revision = await self._snapshot(session_id, connections)
            sent = await self._initial_send(
                connection, snapshot.model_dump(mode="json"), revision
            )
        except BaseException:
            connection.ready.set()
            removed = await self._remove_if_current(connections, connection)
            if removed:
                await self._cancel_handler(connection)
            raise
        if not sent:
            removed = await self._remove_if_current(connections, connection)
            if removed:
                await self._cancel_handler(connection)
            raise HTTPException(status_code=503, detail="Lobby unavailable")
        return connections, connection

    async def publish(self, session_id: UUID) -> None:
        connections = await self._session(session_id, create=False)
        if connections is None:
            return
        timed_out: list[LobbyConnection] = []
        async with connections.lock:
            active = tuple(connections.connections.values())
        if not active:
            return
        snapshot, revision = await self._snapshot(session_id, connections)
        payload = snapshot.model_dump(mode="json")
        results = await asyncio.gather(
            *(self._send(connection, payload, revision) for connection in active)
        )
        for connection, sent in zip(active, results, strict=True):
            if not sent:
                if await self._remove_if_current(connections, connection):
                    timed_out.append(connection)
        for connection in timed_out:
            await self._cancel_handler(connection)
        await asyncio.gather(
            *(self._close(connection.websocket) for connection in timed_out)
        )

    async def disconnect(
        self, websocket: WebSocket, connections: SessionConnections
    ) -> None:
        async with connections.lock:
            connection = connections.connections.pop(websocket, None)
            if connection is not None:
                connection.active = False


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.lobby_hub = LobbyHub()
    yield


app = FastAPI(lifespan=lifespan)
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


def _join_participant(session_id: UUID, payload: ParticipantJoin) -> ParticipantJoined:
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
        finally:
            engine.dispose()
        return response
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503, detail="Participant join unavailable"
        ) from None


@app.post(
    "/sessions/{session_id}/participants",
    status_code=201,
    response_model=ParticipantJoined,
)
async def join_session(
    request: Request, session_id: UUID, payload: ParticipantJoin
) -> ParticipantJoined:
    """Create distinct membership and publish the committed lobby update."""
    response = await run_in_threadpool(_join_participant, session_id, payload)
    try:
        await request.app.state.lobby_hub.publish(session_id)
    except HTTPException:
        # Membership is already committed; reconnecting still returns its state.
        pass
    return response


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
    return _get_lobby_snapshot(session_id, credentials.credentials)


def _websocket_participant_credential(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    offered = [
        protocol.removeprefix("ipo-participant.")
        for protocol in websocket.scope.get("subprotocols", [])
        if protocol.startswith("ipo-participant.")
    ]
    if authorization is not None and offered:
        return None
    if authorization is not None:
        scheme, separator, credential = authorization.partition(" ")
        if scheme.casefold() == "bearer" and separator and credential:
            return credential
        return None
    if len(offered) == 1 and offered[0]:
        return offered[0]
    return None


@app.websocket("/sessions/{session_id}/lobby/ws")
async def lobby_websocket(websocket: WebSocket, session_id: UUID) -> None:
    hub: LobbyHub = websocket.app.state.lobby_hub
    credential = _websocket_participant_credential(websocket)
    if credential is None:
        await hub._close(websocket, code=1008, reason="Invalid participant credentials")
        return
    try:
        await run_in_threadpool(_get_lobby_snapshot, session_id, credential)
    except HTTPException:
        await hub._close(websocket, code=1008, reason="Invalid participant credentials")
        return

    try:
        connections, _ = await hub.connect(
            websocket, session_id, asyncio.current_task()
        )
    except HTTPException:
        await hub._close(websocket, reason="Lobby unavailable")
        return
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            await hub._close(
                websocket, code=1003, reason="Client messages are not supported"
            )
            break
    finally:
        cleanup = asyncio.create_task(hub.disconnect(websocket, connections))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
