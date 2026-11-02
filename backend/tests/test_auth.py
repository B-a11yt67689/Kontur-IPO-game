"""Real login/JWT checks and PostgreSQL evidence for every access refusal."""

import json
import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_sessions import stored_rows


def assert_denied(client: TestClient, headers: dict[str, str], database: str) -> None:
    name = str(uuid4())
    response = client.post("/sessions", json={"name": name}, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert stored_rows(database, name) == []


def test_login_contract(auth_config: dict[str, str], caplog: pytest.LogCaptureFixture):
    before = time.time()
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": auth_config["ADMIN_USERNAME"],
                "password": "fictional-test-password",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    claims = jwt.decode(
        body["access_token"], auth_config["JWT_SIGNING_KEY"], algorithms=["HS256"]
    )
    assert set(claims) == {"sub", "exp"}
    assert claims["sub"] == auth_config["ADMIN_USERNAME"]
    assert before + 1799 <= claims["exp"] <= time.time() + 1800
    for secret in ("fictional-test-password", auth_config["ADMIN_PASSWORD_HASH"]):
        assert secret not in response.text
        assert secret not in caplog.text


def test_wrong_credentials_identical(auth_config: dict[str, str], caplog):
    with TestClient(app) as client:
        responses = [
            client.post(
                "/auth/login", json={"username": username, "password": password}
            )
            for username, password in [
                ("unknown", "fictional-test-password"),
                (auth_config["ADMIN_USERNAME"], "wrong-password"),
                ("unknown", "wrong-password"),
            ]
        ]
    assert all(r.status_code == 401 for r in responses)
    assert all(r.json() == {"detail": "Invalid credentials"} for r in responses)
    assert "fictional-test-password" not in caplog.text
    assert auth_config["ADMIN_PASSWORD_HASH"] not in caplog.text


def test_surrogate_password_safe_refusal(
    auth_config: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    # ensure_ascii sends a literal JSON escape, not an HTTPX encoding failure.
    payload = json.dumps(
        {
            "username": auth_config["ADMIN_USERNAME"],
            "password": "private-password-sentinel\ud800",
        },
        ensure_ascii=True,
    ).encode("ascii")
    assert b"\\ud800" in payload
    with TestClient(app, raise_server_exceptions=False) as client:
        ordinary = client.post(
            "/auth/login",
            json={"username": auth_config["ADMIN_USERNAME"], "password": "wrong"},
        )
        response = client.post(
            "/auth/login",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == ordinary.status_code == 401
    assert response.json() == ordinary.json() == {"detail": "Invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["www-authenticate"] == ordinary.headers["www-authenticate"]
    for secret in (
        "private-password-sentinel",
        auth_config["ADMIN_PASSWORD_HASH"],
        "UnicodeEncodeError",
        "surrogates not allowed",
    ):
        assert secret not in response.text
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"password": "private-password-sentinel"},
        {"username": 42, "password": "private-password-sentinel"},
        {"username": "admin"},
        {"username": "admin", "password": None},
        {"username": "admin", "password": {"hash": "private-hash-sentinel"}},
        {
            "username": "admin",
            "password": "private-password-sentinel",
            "password_hash": "private-hash-sentinel",
        },
        ["private-password-sentinel"],
    ],
)
def test_login_validation_no_reflection(payload, caplog):
    with TestClient(app) as client:
        response = client.post("/auth/login", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid login request"}
    assert "private-password-sentinel" not in caplog.text
    assert "private-hash-sentinel" not in caplog.text


def test_malformed_json_no_reflection():
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            content='{"password":"secret-sentinel",',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid login request"}


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic abc",
        "Bearer",
        "Bearer ",
        "Bearer broken",
        "Bearer a.b.c",
        "Bearer x y",
    ],
)
def test_bad_bearer_denied(header, auth_config, migrated_database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with TestClient(app) as client:
        assert_denied(
            client,
            {} if header is None else {"Authorization": header},
            migrated_database,
        )


@pytest.mark.parametrize(
    "variant",
    [
        "expired",
        "tampered",
        "no-sub",
        "no-exp",
        "foreign",
        "none",
        "HS384",
        "exp-list",
        "exp-object",
        "exp-text",
        "sub-number",
    ],
)
def test_bad_jwt_denied(variant, auth_config, migrated_database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    claims = {"sub": auth_config["ADMIN_USERNAME"], "exp": int(time.time()) + 1800}
    algorithm = "HS256"
    key = auth_config["JWT_SIGNING_KEY"]
    if variant == "expired":
        claims["exp"] = int(time.time()) - 1
    elif variant == "tampered":
        key = "fictional-different-signing-key-0000000000"
    elif variant == "no-sub":
        del claims["sub"]
    elif variant == "no-exp":
        del claims["exp"]
    elif variant == "foreign":
        claims["sub"] = "other-admin"
    elif variant in ("none", "HS384"):
        algorithm = variant
        if variant == "none":
            key = None
    elif variant.startswith("exp-"):
        claims["exp"] = {"exp-list": [], "exp-object": {}, "exp-text": "invalid"}[
            variant
        ]
    elif variant == "sub-number":
        claims["sub"] = 42
    token = jwt.encode(claims, key, algorithm=algorithm)
    with TestClient(app) as client:
        assert_denied(client, {"Authorization": "Bearer " + token}, migrated_database)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        (setting, value)
        for setting in ("ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "JWT_SIGNING_KEY")
        for value in (None, "", " ")
    ]
    + [
        ("ADMIN_PASSWORD_HASH", "invalid-hash"),
        ("ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$bad$broken"),
        ("JWT_SIGNING_KEY", "too-short"),
        ("JWT_SIGNING_KEY", " " * 40),
    ],
)
def test_invalid_config_closed(
    setting, value, auth_config, admin_headers, migrated_database, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    if value is None:
        monkeypatch.delenv(setting)
    else:
        monkeypatch.setenv(setting, value)
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": auth_config["ADMIN_USERNAME"],
                "password": "fictional-test-password",
            },
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}
        assert_denied(client, admin_headers, migrated_database)
        assert client.get("/health").json() == {"status": "ok"}


def test_auth_precedes_database(auth_config, monkeypatch):
    import app.main as main

    def forbidden_database():
        pytest.fail("Unauthorized request reached database creation")

    monkeypatch.setattr(main, "create_database_engine", forbidden_database)
    monkeypatch.setenv("DATABASE_URL", "invalid")
    with TestClient(app) as client:
        assert client.post("/sessions", json={"name": "example"}).status_code == 401


def test_health_with_no_configuration(monkeypatch):
    for name in (
        "DATABASE_URL",
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD_HASH",
        "JWT_SIGNING_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
