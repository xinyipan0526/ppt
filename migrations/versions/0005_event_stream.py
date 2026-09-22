"""Add versioned replay metadata and atomic per-Run event allocation.

Revision ID: 0005_event_stream
Revises: 0004_artifact_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_event_stream"
down_revision: str | None = "0004_artifact_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "next_event_seq", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.execute(
        """
        UPDATE workflow_runs
        SET next_event_seq = COALESCE(
            (SELECT MAX(run_seq) + 1
             FROM workflow_events
             WHERE workflow_events.run_id = workflow_runs.id),
            1
        )
        """
    )

    with op.batch_alter_table("workflow_events") as batch_op:
        batch_op.alter_column(
            "created_at",
            new_column_name="recorded_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
        batch_op.add_column(sa.Column("step_id", sa.String(100)))
        batch_op.add_column(sa.Column("page_id", sa.String(100)))
        batch_op.add_column(sa.Column("operation_id", sa.String(100)))
        batch_op.add_column(sa.Column("trace_id", sa.String(100)))
        batch_op.add_column(
            sa.Column(
                "durability",
                sa.Enum(
                    "core",
                    "ephemeral",
                    name="event_durability",
                    native_enum=False,
                    create_constraint=True,
                ),
                nullable=False,
                server_default="core",
            )
        )

    # Legacy events did not distinguish occurrence from persistence time. The
    # only honest value is their original created_at, now named recorded_at.
    op.execute("UPDATE workflow_events SET occurred_at = recorded_at")

    op.create_index("ix_workflow_events_step_id", "workflow_events", ["step_id"])
    op.create_index("ix_workflow_events_page_id", "workflow_events", ["page_id"])
    op.create_index(
        "ix_workflow_events_operation_id", "workflow_events", ["operation_id"]
    )
    op.create_index("ix_workflow_events_trace_id", "workflow_events", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_events_trace_id", table_name="workflow_events")
    op.drop_index("ix_workflow_events_operation_id", table_name="workflow_events")
    op.drop_index("ix_workflow_events_page_id", table_name="workflow_events")
    op.drop_index("ix_workflow_events_step_id", table_name="workflow_events")
    with op.batch_alter_table("workflow_events") as batch_op:
        batch_op.drop_column("durability")
        batch_op.drop_column("trace_id")
        batch_op.drop_column("operation_id")
        batch_op.drop_column("page_id")
        batch_op.drop_column("step_id")
        batch_op.drop_column("occurred_at")
        batch_op.drop_column("schema_version")
        batch_op.alter_column(
            "recorded_at",
            new_column_name="created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
    op.drop_column("workflow_runs", "next_event_seq")
