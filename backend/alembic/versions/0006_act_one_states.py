"""Persist the first act's state separately from preparation and participants."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_act_one_states"
down_revision = "0005_role_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "act_one_states",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("balance", sa.Numeric(), nullable=False),
        sa.Column("clients", sa.Integer(), nullable=False),
        sa.Column("action_points", sa.Integer(), nullable=False),
        sa.Column("hiring_level", sa.Integer(), nullable=False),
        sa.Column("hiring_capacity", sa.Integer(), nullable=False),
        sa.Column("hired_development_team_grades", postgresql.JSONB(), nullable=False),
        sa.Column("experiments_per_development_team", sa.Integer(), nullable=False),
        sa.Column("starting_roll", sa.Integer(), nullable=False),
        sa.Column("starting_bonus_available", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["game_sessions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("clients >= 0", name="ck_act_one_states_clients"),
        sa.CheckConstraint(
            "action_points >= 0", name="ck_act_one_states_action_points"
        ),
        sa.CheckConstraint(
            "experiments_per_development_team > 0",
            name="ck_act_one_states_experiments",
        ),
        sa.CheckConstraint(
            "starting_roll BETWEEN 1 AND 10", name="ck_act_one_states_starting_roll"
        ),
        sa.CheckConstraint(
            "hiring_level BETWEEN 0 AND 1", name="ck_act_one_states_hiring_level"
        ),
        sa.CheckConstraint(
            "(hiring_level = 0 AND hiring_capacity = 2) OR "
            "(hiring_level = 1 AND hiring_capacity = 3)",
            name="ck_act_one_states_hiring_capacity",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(hired_development_team_grades) = 'array'",
            name="ck_act_one_states_team_grades_array",
        ),
    )


def downgrade() -> None:
    op.drop_table("act_one_states")
