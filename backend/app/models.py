"""Provisional persistence model: one game session represents one team."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GameSession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) <= 255", name="ck_game_sessions_name_length"
        ),
        ForeignKeyConstraint(
            ["id", "captain_participant_id"],
            ["participants.session_id", "participants.id"],
            name="fk_game_sessions_captain_participant",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'lobby'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    captain_participant_id: Mapped[UUID | None] = mapped_column(Uuid)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        CheckConstraint(
            "char_length(display_name) <= 255",
            name="ck_participants_display_name_length",
        ),
        CheckConstraint(
            "char_length(credential_digest) = 64",
            name="ck_participants_credential_digest_length",
        ),
        UniqueConstraint("session_id", "id", name="uq_participants_session_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    credential_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role_code IN ('cto', 'cmo', 'people', 'cfo')",
            name="ck_role_assignments_role_code",
        ),
        UniqueConstraint(
            "session_id", "participant_id", name="uq_role_assignments_participant"
        ),
        ForeignKeyConstraint(
            ["session_id", "participant_id"],
            ["participants.session_id", "participants.id"],
            name="fk_role_assignments_participant",
            ondelete="CASCADE",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    role_code: Mapped[str] = mapped_column(Text, primary_key=True)
    participant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
