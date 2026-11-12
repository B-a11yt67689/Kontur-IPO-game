"""Validated HTTP payloads for game session creation."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


class SessionCreate(BaseModel):
    # Unknown fields, including server-owned values, cannot affect persistence.
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]


class SessionCreated(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    created_at: datetime


class CaptainAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: UUID


class CaptainAssigned(BaseModel):
    session_id: UUID
    captain_participant_id: UUID


RoleCode = Literal["cto", "cmo", "people", "cfo"]


class RoleAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: UUID


class RoleAssigned(BaseModel):
    session_id: UUID
    role_code: RoleCode
    participant_id: UUID


class ParticipantJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]


class ParticipantJoined(BaseModel):
    id: UUID
    display_name: str
    participant_credential: str
    token_type: str = "bearer"


class ParticipantIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    created_at: datetime


class LobbyParticipant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str


class LobbySnapshot(BaseModel):
    id: UUID
    name: str
    status: str
    participants: list[LobbyParticipant]
