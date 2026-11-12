"""Persist role claims scoped to a session and its participants."""

import sqlalchemy as sa

from alembic import op

revision = "0005_role_assignments"
down_revision = "0004_session_captain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_assignments",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.Text(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "role_code"),
        sa.CheckConstraint(
            "role_code IN ('cto', 'cmo', 'people', 'cfo')",
            name="ck_role_assignments_role_code",
        ),
        sa.UniqueConstraint(
            "session_id", "participant_id", name="uq_role_assignments_participant"
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "participant_id"],
            ["participants.session_id", "participants.id"],
            name="fk_role_assignments_participant",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("role_assignments")
