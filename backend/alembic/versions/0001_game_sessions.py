"""Create the initial game session table."""

import sqlalchemy as sa

from alembic import op

revision = "0001_game_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_sessions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'lobby'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_game_sessions"),
    )


def downgrade() -> None:
    op.drop_table("game_sessions")
