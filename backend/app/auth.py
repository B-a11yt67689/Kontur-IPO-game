"""Lazy single-administrator authentication; no database or startup configuration."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALGORITHM = "HS256"
TOKEN_SECONDS = 1800
password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True, repr=False)
class AdminConfig:
    username: str
    password_hash: str
    signing_key: str


def admin_config() -> AdminConfig:
    """Reject absent settings and unusable hashes, including on token access."""
    username = os.environ.get("ADMIN_USERNAME", "")
    hashed = os.environ.get("ADMIN_PASSWORD_HASH", "")
    key = os.environ.get("JWT_SIGNING_KEY", "")
    if (
        not username.strip()
        or not hashed
        or not key.strip()
        or len(key.encode("utf-8")) < 32
    ):
        raise unauthorized()
    try:
        # A mismatch proves the configured hash can actually be verified.
        # Reject an empty administrator password as invalid configuration.
        password_hasher.verify(hashed, "")
    except VerifyMismatchError:
        return AdminConfig(username, hashed, key)
    except (VerificationError, InvalidHashError):
        pass
    raise unauthorized()


def login_token(username: str, password: str) -> str:
    config = admin_config()
    try:
        # Verify even for an unknown username; return the same safe failure.
        valid = password_hasher.verify(config.password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeEncodeError):
        valid = False
    if not valid or username != config.username:
        raise unauthorized()
    return jwt.encode(
        {
            "sub": config.username,
            "exp": datetime.now(UTC) + timedelta(seconds=TOKEN_SECONDS),
        },
        config.signing_key,
        algorithm=ALGORITHM,
    )


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    if credentials is None:
        raise unauthorized()
    config = admin_config()
    try:
        claims = jwt.decode(
            credentials.credentials,
            config.signing_key,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        if claims["sub"] != config.username:
            raise unauthorized()
    except (jwt.InvalidTokenError, TypeError, ValueError, OverflowError):
        raise unauthorized() from None
