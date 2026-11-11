"""Exercise the lobby over a loopback Uvicorn process and a dedicated test DB."""

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from websockets.exceptions import InvalidStatus
from websockets.sync.client import ClientConnection, connect

from app.database import database_url

HOST = "127.0.0.1"
BACKEND = Path(__file__).resolve().parent
START_TIMEOUT = 15
IO_TIMEOUT = 5
ADMIN = "fictional-ws-smoke-admin"
PASSWORD = "fictional-ws-smoke-password"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def request(
    port: int,
    method: str,
    path: str,
    expected_status: int,
    *,
    payload: dict[str, str] | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    body = json.dumps(payload) if payload is not None else None
    connection = HTTPConnection(HOST, port, timeout=IO_TIMEOUT)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        print(f"{method} {path}: HTTP {response.status}", flush=True)
        check(response.status == expected_status, f"Expected HTTP {expected_status}")
        return json.loads(response.read())
    finally:
        connection.close()


def connect_lobby(uri: str, credential: str | None = None) -> ClientConnection:
    protocols = [f"ipo-participant.{credential}"] if credential is not None else None
    return connect(
        uri,
        subprotocols=protocols,
        proxy=None,
        open_timeout=IO_TIMEOUT,
        close_timeout=2,
    )


def check_snapshot(
    websocket: ClientConnection,
    expected: dict[str, Any],
    credentials: list[str],
    label: str,
) -> None:
    response = websocket.response
    check(response is not None and response.status_code == 101, "Expected HTTP 101")
    check(websocket.subprotocol is None, "Unexpected agreed subprotocol")
    frame = websocket.recv(timeout=IO_TIMEOUT)
    if not isinstance(frame, str):
        raise RuntimeError(f"{label}: expected a text frame")
    for credential in credentials:
        digest = hashlib.sha256(credential.encode()).hexdigest()
        check(
            credential not in frame and digest not in frame, f"{label}: credential leak"
        )
    check(json.loads(frame) == expected, f"{label}: unexpected lobby snapshot")
    print(f"{label}: HTTP 101, no agreed subprotocol, expected snapshot", flush=True)


def check_rejected(uri: str, label: str, credential: str | None = None) -> None:
    try:
        with connect_lobby(uri, credential):
            raise RuntimeError(f"{label}: unexpectedly accepted")
    except InvalidStatus as error:
        status = error.response.status_code
        print(f"{label}: HTTP {status}", flush=True)
        check(status == 403, "Expected HTTP 403")


def run_scenario(port: int) -> None:
    check(request(port, "GET", "/health", 200) == {"status": "ok"}, "Unexpected health")
    login = request(
        port,
        "POST",
        "/auth/login",
        200,
        payload={"username": ADMIN, "password": PASSWORD},
    )
    token = login["access_token"]
    session_name = "WebSocket smoke team"
    session = request(
        port, "POST", "/sessions", 201, payload={"name": session_name}, bearer=token
    )
    session_path = f"/sessions/{session['id']}"
    first = request(
        port,
        "POST",
        f"{session_path}/participants",
        201,
        payload={"display_name": "First"},
    )
    credential = first["participant_credential"]
    expected = {
        "id": session["id"],
        "name": session_name,
        "status": "lobby",
        "participants": [{"id": first["id"], "display_name": "First"}],
    }
    snapshot = request(port, "GET", f"{session_path}/lobby", 200, bearer=credential)
    check(snapshot == expected, "Unexpected HTTP lobby snapshot")

    uri = f"ws://{HOST}:{port}{session_path}/lobby/ws"
    with connect_lobby(uri, credential) as websocket:
        check_snapshot(websocket, expected, [credential], "Initial frame")
        second = request(
            port,
            "POST",
            f"{session_path}/participants",
            201,
            payload={"display_name": "Second"},
        )
        expected["participants"].append({"id": second["id"], "display_name": "Second"})
        credentials = [credential, second["participant_credential"]]
        check_snapshot(websocket, expected, credentials, "Join update")

    with connect_lobby(uri, credential) as websocket:
        check_snapshot(websocket, expected, credentials, "Reconnect frame")

    other_session = request(
        port, "POST", "/sessions", 201, payload={"name": "Other team"}, bearer=token
    )
    other = request(
        port,
        "POST",
        f"/sessions/{other_session['id']}/participants",
        201,
        payload={"display_name": "Other"},
    )
    check_rejected(uri, "Other session credential", other["participant_credential"])
    check_rejected(uri, "Missing credential")


def wait_for_port(server: subprocess.Popen, log_path: Path) -> int:
    deadline = time.monotonic() + START_TIMEOUT
    while server.poll() is None and time.monotonic() < deadline:
        log = log_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", log)
        if match:
            return int(match[1])
        time.sleep(0.05)
    raise RuntimeError("Uvicorn did not start within the startup deadline")


def stop_server(server: subprocess.Popen) -> None:
    if server.poll() is not None:
        return
    if sys.platform == "win32":
        # The Windows venv launcher starts Uvicorn in a child process.
        taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
        subprocess.run(
            [taskkill, "/PID", str(server.pid), "/T", "/F"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        server.wait(timeout=5)
        return

    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def run_server(server_python: str, test_database: str) -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": test_database,
        "ADMIN_USERNAME": ADMIN,
        "ADMIN_PASSWORD_HASH": PasswordHasher().hash(PASSWORD),
        "JWT_SIGNING_KEY": secrets.token_urlsafe(48),
    }
    with tempfile.TemporaryDirectory(prefix="ipo-ws-smoke-") as directory:
        log_path = Path(directory) / "uvicorn.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [
                    server_python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    HOST,
                    "--port",
                    "0",
                ],
                cwd=BACKEND,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            try:
                run_scenario(wait_for_port(server, log_path))
            finally:
                stop_server(server)
        except Exception:
            print(
                log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-python", default=sys.executable)
    args = parser.parse_args()
    test_database = os.environ.get("TEST_DATABASE_URL")
    if not test_database or not database_url(test_database).database.endswith("_test"):
        parser.error("Set TEST_DATABASE_URL to a dedicated, migrated *_test database")
    try:
        run_server(args.server_python, test_database)
    except InvalidStatus as error:
        sys.exit(f"WebSocket upgrade returned HTTP {error.response.status_code}")


if __name__ == "__main__":
    main()
