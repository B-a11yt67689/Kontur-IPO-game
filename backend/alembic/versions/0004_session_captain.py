"""Persist one captain drawn from the session's participants."""

import sqlalchemy as sa

from alembic import op

revision = "0004_session_captain"
down_revision = "0003_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_participants_session_id_id", "participants", ["session_id", "id"]
    )
    op.add_column(
        "game_sessions", sa.Column("captain_participant_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_game_sessions_captain_participant",
        "game_sessions",
        "participants",
        ["id", "captain_participant_id"],
        ["session_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_game_sessions_captain_participant", "game_sessions", type_="foreignkey"
    )
    op.drop_column("game_sessions", "captain_participant_id")
    op.drop_constraint("uq_participants_session_id_id", "participants", type_="unique")
