"""Authenticated real-time lobby updates over WebSocket."""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.main import LobbyConnection, LobbyHub, app
from app.schemas import LobbySnapshot


@pytest.fixture
def client(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    admin_headers: dict[str, str],
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app, headers=admin_headers) as test_client:
        yield test_client


def create_session(client: TestClient) -> UUID:
    response = client.post("/sessions", json={"name": "IPO team"})
    assert response.status_code == 201
    return UUID(response.json()["id"])


def join(client: TestClient, session_id: UUID, display_name: str) -> dict[str, str]:
    response = client.post(
        f"/sessions/{session_id}/participants",
        json={"display_name": display_name},
    )
    assert response.status_code == 201
    return response.json()


def assert_no_secret(payload: dict, credentials: list[str]) -> None:
    serialized = json.dumps(payload)
    assert "participant_credential" not in serialized
    for credential in credentials:
        assert credential not in serialized
        assert hashlib.sha256(credential.encode("utf-8")).hexdigest() not in serialized


def websocket_url(session_id: UUID) -> str:
    return f"/sessions/{session_id}/lobby/ws"


def clear_default_authorization(client: TestClient) -> None:
    client.headers.pop("Authorization", None)


def test_initial_snapshot_updates_on_join_and_reconnect(client: TestClient) -> None:
    session_id = create_session(client)
    clear_default_authorization(client)
    first = join(client, session_id, "First")
    second: dict[str, str] | None = None
    expected_initial = {
        "id": str(session_id),
        "name": "IPO team",
        "status": "lobby",
        "participants": [{"id": first["id"], "display_name": "First"}],
    }

    with client.websocket_connect(
        websocket_url(session_id),
        subprotocols=[f"ipo-participant.{first['participant_credential']}"],
    ) as websocket:
        assert websocket.accepted_subprotocol is None
        initial = websocket.receive_json()
        assert initial == expected_initial
        assert_no_secret(initial, [first["participant_credential"]])

        second = join(client, session_id, "Second")
        update = websocket.receive_json()
        assert update == {
            **expected_initial,
            "participants": [
                {"id": first["id"], "display_name": "First"},
                {"id": second["id"], "display_name": "Second"},
            ],
        }
        assert_no_secret(
            update,
            [first["participant_credential"], second["participant_credential"]],
        )

    assert second is not None
    with client.websocket_connect(
        websocket_url(session_id),
        headers={"Authorization": f"Bearer {second['participant_credential']}"},
    ) as reconnected:
        snapshot = reconnected.receive_json()
        assert snapshot["participants"] == [
            {"id": first["id"], "display_name": "First"},
            {"id": second["id"], "display_name": "Second"},
        ]
        assert_no_secret(
            snapshot,
            [first["participant_credential"], second["participant_credential"]],
        )


def test_websocket_rejects_missing_invalid_and_wrong_session_credentials(
    client: TestClient,
) -> None:
    first_session = create_session(client)
    second_session = create_session(client)
    clear_default_authorization(client)
    first = join(client, first_session, "First")
    second = join(client, second_session, "Second")
    attempts = [
        ([], {}, first_session),
        (
            [f"ipo-participant.invalid-{uuid4()}"],
            {},
            first_session,
        ),
        (
            [f"ipo-participant.{second['participant_credential']}"],
            {},
            first_session,
        ),
    ]

    for subprotocols, headers, session_id in attempts:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                websocket_url(session_id),
                subprotocols=subprotocols,
                headers=headers,
            ):
                pytest.fail("invalid participant credential was accepted")
        assert denied.value.code == 1008
        assert denied.value.reason == "Invalid participant credentials"
        assert first["participant_credential"] not in denied.value.reason
        assert second["participant_credential"] not in denied.value.reason

    with pytest.raises(WebSocketDisconnect) as query_denied:
        with client.websocket_connect(
            websocket_url(first_session)
            + f"?participant_credential={first['participant_credential']}"
        ):
            pytest.fail("query-string credentials must not authenticate")
    assert query_denied.value.code == 1008
    assert first["participant_credential"] not in query_denied.value.reason


def test_disconnected_socket_is_removed_before_later_joins(client: TestClient) -> None:
    session_id = create_session(client)
    clear_default_authorization(client)
    participant = join(client, session_id, "First")

    with client.websocket_connect(
        websocket_url(session_id),
        subprotocols=[f"ipo-participant.{participant['participant_credential']}"],
    ) as websocket:
        assert websocket.receive_json()["participants"] == [
            {"id": participant["id"], "display_name": "First"}
        ]

    connections = app.state.lobby_hub._sessions.get(session_id)
    assert connections is None or not connections.connections
    later_join = join(client, session_id, "After disconnect")
    assert later_join["display_name"] == "After disconnect"


def test_stalled_socket_does_not_block_another_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inline_threadpool(function, *args):
        return function(*args)

    hub = LobbyHub()
    slow_session = uuid4()
    fast_session = uuid4()
    slow_send_started = asyncio.Event()
    release_slow_send = asyncio.Event()
    fast_send_completed = asyncio.Event()
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    class FakeWebSocket:
        def __init__(self, *, slow: bool = False) -> None:
            self.slow = slow
            self.block_next_send = False
            self.session_state = None
            self.closed = False

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            assert not hub._registry_lock.locked()
            if self.slow and self.block_next_send:
                self.block_next_send = False
                slow_send_started.set()
                await release_slow_send.wait()
            if not self.slow:
                fast_send_completed.set()

        async def close(self, *, code: int, reason: str) -> None:
            assert code == 1011
            assert reason in {"Lobby send timed out", "Lobby unavailable"}
            assert not hub._registry_lock.locked()
            assert self.session_state is None or not self.session_state.lock.locked()
            self.closed = True
            close_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                close_cancelled.set()

    def snapshot(session_id: UUID) -> LobbySnapshot:
        assert not hub._registry_lock.locked()
        return LobbySnapshot(
            id=session_id,
            name="IPO team",
            status="lobby",
            participants=[],
        )

    monkeypatch.setattr(main, "_get_lobby_snapshot", snapshot)
    monkeypatch.setattr(main, "run_in_threadpool", inline_threadpool)

    async def verify_concurrency() -> None:
        slow_socket = FakeWebSocket(slow=True)
        fast_socket = FakeWebSocket()
        slow_state, _ = await hub.connect(slow_socket, slow_session)
        fast_state, _ = await hub.connect(fast_socket, fast_session)
        slow_socket.session_state = slow_state
        fast_socket.session_state = fast_state
        fast_send_completed.clear()
        slow_socket.block_next_send = True

        stalled_publish = asyncio.create_task(hub.publish(slow_session))
        await slow_send_started.wait()
        other_publish = asyncio.create_task(hub.publish(fast_session))
        try:
            await asyncio.wait_for(fast_send_completed.wait(), timeout=2)
            await other_publish
        finally:
            release_slow_send.set()
        await stalled_publish
        await hub.disconnect(fast_socket, fast_state)

        release_slow_send.clear()
        slow_send_started.clear()
        slow_socket.block_next_send = True
        hub.send_timeout = 0.01
        hub.close_timeout = 0.01
        await hub.publish(slow_session)
        assert slow_send_started.is_set()
        assert slow_socket not in slow_state.connections
        assert close_started.is_set()
        assert close_cancelled.is_set()
        assert slow_socket.closed
        await hub.disconnect(slow_socket, slow_state)

    asyncio.run(verify_concurrency())


def test_stalled_send_does_not_block_same_session_disconnect_or_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inline_threadpool(function, *args):
        return function(*args)

    hub = LobbyHub()
    session_id = uuid4()
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    class FakeWebSocket:
        def __init__(self, *, stalled: bool = False) -> None:
            self.stalled = stalled
            self.block_send = False

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            if self.stalled and self.block_send:
                send_started.set()
                await release_send.wait()
                raise OSError("simulated failed send")

        async def close(self, *, code: int, reason: str) -> None:
            pass

    def snapshot(requested_session_id: UUID) -> LobbySnapshot:
        return LobbySnapshot(
            id=requested_session_id,
            name="IPO team",
            status="lobby",
            participants=[],
        )

    monkeypatch.setattr(main, "_get_lobby_snapshot", snapshot)
    monkeypatch.setattr(main, "run_in_threadpool", inline_threadpool)

    async def verify_session_operations() -> None:
        old_socket = FakeWebSocket(stalled=True)
        connections, _ = await hub.connect(old_socket, session_id)
        old_socket.block_send = True
        publish = asyncio.create_task(hub.publish(session_id))
        await asyncio.wait_for(send_started.wait(), timeout=1)

        await asyncio.wait_for(hub.disconnect(old_socket, connections), timeout=1)
        replacement = FakeWebSocket()
        await asyncio.wait_for(hub.connect(replacement, session_id), timeout=1)
        release_send.set()
        await asyncio.wait_for(publish, timeout=1)
        assert replacement in connections.connections
        assert old_socket not in connections.connections
        await hub.disconnect(replacement, connections)

    asyncio.run(verify_session_operations())


def test_initial_snapshot_precedes_later_ordered_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = LobbyHub()
    session_id = uuid4()
    snapshot_calls = 0
    stale_send_waiting = asyncio.Event()

    class ObservableReady:
        def __init__(self) -> None:
            self.event = asyncio.Event()

        async def wait(self) -> None:
            stale_send_waiting.set()
            await self.event.wait()

        def set(self) -> None:
            self.event.set()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.snapshots: list[str] = []

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            self.snapshots.append(payload["status"])

    def snapshot(requested_session_id: UUID) -> LobbySnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return LobbySnapshot(
            id=requested_session_id,
            name="IPO team",
            status="older-publish" if snapshot_calls == 1 else "newer-initial",
            participants=[],
        )

    async def controlled_threadpool(function, *args):
        return function(*args)

    monkeypatch.setattr(main, "_get_lobby_snapshot", snapshot)
    monkeypatch.setattr(main, "run_in_threadpool", controlled_threadpool)

    async def verify_ordering() -> None:
        session = await hub._session(session_id, create=True)
        assert session is not None
        websocket = FakeWebSocket()
        connection = LobbyConnection(websocket, None)
        connection.ready = ObservableReady()
        async with session.lock:
            session.connections[websocket] = connection

        old_snapshot, old_revision = await hub._snapshot(session_id, session)
        stale_publish = asyncio.create_task(
            hub._send(
                connection,
                old_snapshot.model_dump(mode="json"),
                old_revision,
            )
        )
        await stale_send_waiting.wait()
        newer_snapshot, newer_revision = await hub._snapshot(session_id, session)
        assert await hub._initial_send(
            connection,
            newer_snapshot.model_dump(mode="json"),
            newer_revision,
        )
        assert await stale_publish
        assert websocket.snapshots == ["newer-initial"]

    asyncio.run(verify_ordering())


def test_timed_out_publish_cancels_handler_and_unregisters_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inline_threadpool(function, *args):
        return function(*args)

    hub = LobbyHub()
    session_id = uuid4()
    receive_started = asyncio.Event()
    send_started = asyncio.Event()
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()
    cleanup_completed = asyncio.Event()

    class FakeWebSocket:
        app = app

        def __init__(self) -> None:
            self.block_send = False

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            if self.block_send:
                send_started.set()
                await asyncio.Event().wait()

        async def close(self, *, code: int, reason: str) -> None:
            assert code == 1011
            assert reason in {"Lobby send timed out", "Lobby unavailable"}
            assert not hub._registry_lock.locked()
            state = hub._sessions.get(session_id)
            assert state is None or not state.lock.locked()
            self.closed = True
            close_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                close_cancelled.set()

        async def receive(self) -> dict:
            receive_started.set()
            await asyncio.Event().wait()
            return {"type": "websocket.disconnect"}

    def snapshot(requested_session_id: UUID, credential: str | None = None):
        return LobbySnapshot(
            id=requested_session_id,
            name="IPO team",
            status="lobby",
            participants=[],
        )

    monkeypatch.setattr(main, "_get_lobby_snapshot", snapshot)
    monkeypatch.setattr(main, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(
        main, "_websocket_participant_credential", lambda websocket: "valid-token"
    )
    disconnect = hub.disconnect

    async def tracked_disconnect(websocket, connections) -> None:
        await disconnect(websocket, connections)
        cleanup_completed.set()

    monkeypatch.setattr(hub, "disconnect", tracked_disconnect)
    app.state.lobby_hub = hub
    websocket = FakeWebSocket()

    async def verify_termination() -> None:
        hub.send_timeout = 0.01
        hub.close_timeout = 0.01
        handler = asyncio.create_task(main.lobby_websocket(websocket, session_id))
        await asyncio.wait_for(receive_started.wait(), timeout=1)
        websocket.block_send = True
        connections = hub._sessions.get(session_id)
        assert connections is not None
        assert websocket in connections.connections

        await hub.publish(session_id)
        assert send_started.is_set()
        assert close_started.is_set()
        assert close_cancelled.is_set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler, timeout=1)
        assert cleanup_completed.is_set()
        assert websocket not in connections.connections

        close_started.clear()
        close_cancelled.clear()
        receive_started.clear()
        cancellation_probe = FakeWebSocket()
        handler = asyncio.create_task(
            main.lobby_websocket(cancellation_probe, session_id)
        )
        await asyncio.wait_for(receive_started.wait(), timeout=1)
        cancellation_probe.block_send = True
        connections = hub._sessions.get(session_id)
        assert connections is not None
        publish = asyncio.create_task(hub.publish(session_id))
        await asyncio.wait_for(close_started.wait(), timeout=1)
        publish.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(publish, timeout=1)
        assert close_cancelled.is_set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler, timeout=1)
        assert cleanup_completed.is_set()
        assert cancellation_probe not in connections.connections

        close_started.clear()
        close_cancelled.clear()
        receive_started.clear()
        initial_failure = FakeWebSocket()
        initial_failure.block_send = True
        initial_failure_handler = asyncio.create_task(
            main.lobby_websocket(initial_failure, session_id)
        )
        await asyncio.wait_for(initial_failure_handler, timeout=1)
        assert close_started.is_set()
        assert close_cancelled.is_set()
        assert initial_failure.closed
        assert not receive_started.is_set()
        connections = hub._sessions.get(session_id)
        assert connections is None or initial_failure not in connections.connections

    asyncio.run(verify_termination())
