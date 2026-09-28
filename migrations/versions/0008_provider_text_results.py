"""Persist validated text results before selecting provider success.

Revision ID: 0008_provider_text_results
Revises: 0007_planning_runtime
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008_provider_text_results"
down_revision: str | None = "0007_planning_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_text_results",
        sa.Column(
            "operation_id",
            sa.String(100),
            sa.ForeignKey("provider_operations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "attempt_id",
            sa.String(100),
            sa.ForeignKey("provider_attempts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("schema_name", sa.String(150), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_text_results")
