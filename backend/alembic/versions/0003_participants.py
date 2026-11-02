"""Persist session-scoped participant identities."""

import sqlalchemy as sa

from alembic import op

revision = "0003_participants"
down_revision = "0002_session_name_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participants",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("credential_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(display_name) <= 255",
            name="ck_participants_display_name_length",
        ),
        sa.CheckConstraint(
            "char_length(credential_digest) = 64",
            name="ck_participants_credential_digest_length",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name="fk_participants_session_id_game_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_participants"),
    )
    op.create_index(
        "ix_participants_session_id", "participants", ["session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_participants_session_id", table_name="participants")
    op.drop_table("participants")
