"""Limit session names without truncating existing data."""

from alembic import op

revision = "0002_session_name_length"
down_revision = "0001_game_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_game_sessions_name_length", "game_sessions", "char_length(name) <= 255"
    )


def downgrade() -> None:
    op.drop_constraint("ck_game_sessions_name_length", "game_sessions", type_="check")
