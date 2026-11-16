"""Provisional persistence model: one game session represents one team."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class ActOneState(Base):
    __tablename__ = "act_one_states"
    __table_args__ = (
        CheckConstraint("clients >= 0", name="ck_act_one_states_clients"),
        CheckConstraint("action_points >= 0", name="ck_act_one_states_action_points"),
        CheckConstraint(
            "experiments_per_development_team > 0",
            name="ck_act_one_states_experiments",
        ),
        CheckConstraint(
            "starting_roll BETWEEN 1 AND 10", name="ck_act_one_states_starting_roll"
        ),
        CheckConstraint(
            "hiring_level BETWEEN 0 AND 1", name="ck_act_one_states_hiring_level"
        ),
        CheckConstraint(
            "(hiring_level = 0 AND hiring_capacity = 2) OR "
            "(hiring_level = 1 AND hiring_capacity = 3)",
            name="ck_act_one_states_hiring_capacity",
        ),
        CheckConstraint(
            "jsonb_typeof(hired_development_team_grades) = 'array'",
            name="ck_act_one_states_team_grades_array",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    balance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    clients: Mapped[int] = mapped_column(Integer, nullable=False)
    action_points: Mapped[int] = mapped_column(Integer, nullable=False)
    hiring_level: Mapped[int] = mapped_column(Integer, nullable=False)
    hiring_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    hired_development_team_grades: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False
    )
    experiments_per_development_team: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    starting_roll: Mapped[int] = mapped_column(Integer, nullable=False)
    starting_bonus_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
