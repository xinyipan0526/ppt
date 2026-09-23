"""Add immutable planning input generations.

Revision ID: 0007_planning_runtime
Revises: 0006_runtime_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_planning_runtime"
down_revision: str | None = "0006_runtime_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("revision_scope", sa.String(20)),
        sa.Column("page_indexes", sa.JSON(), nullable=False),
        sa.Column("clarification_answers", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "run_id", "generation", name="uq_planning_input_generation"
        ),
    )
    op.create_index("ix_planning_inputs_run_id", "planning_inputs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_planning_inputs_run_id", table_name="planning_inputs")
    op.drop_table("planning_inputs")
