"""Add durable retry, recovery, and provider ownership metadata.

Revision ID: 0006_runtime_recovery
Revises: 0005_event_stream
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_runtime_recovery"
down_revision: str | None = "0005_event_stream"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("retry_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("recovery_metadata", sa.JSON()))
        batch.create_index("ix_workflow_runs_status_retry", ["status", "retry_at"])
    with op.batch_alter_table("workflow_steps") as batch:
        batch.add_column(sa.Column("retry_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("recovery_metadata", sa.JSON()))
        batch.create_index("ix_workflow_steps_status_retry", ["status", "retry_at"])
    with op.batch_alter_table("pages") as batch:
        batch.add_column(sa.Column("recovery_metadata", sa.JSON()))
    with op.batch_alter_table("provider_operations") as batch:
        batch.add_column(
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey(
                    "workflow_runs.id",
                    name="fk_provider_operations_run_id",
                    ondelete="CASCADE",
                ),
            )
        )
        batch.add_column(
            sa.Column(
                "step_id",
                sa.String(36),
                sa.ForeignKey(
                    "workflow_steps.id",
                    name="fk_provider_operations_step_id",
                    ondelete="SET NULL",
                ),
            )
        )
        batch.add_column(
            sa.Column(
                "page_id",
                sa.String(36),
                sa.ForeignKey(
                    "pages.id",
                    name="fk_provider_operations_page_id",
                    ondelete="SET NULL",
                ),
            )
        )
        batch.create_index("ix_provider_operations_run_id", ["run_id"])
        batch.create_index("ix_provider_operations_step_id", ["step_id"])
        batch.create_index("ix_provider_operations_page_id", ["page_id"])


def downgrade() -> None:
    with op.batch_alter_table("provider_operations") as batch:
        batch.drop_index("ix_provider_operations_page_id")
        batch.drop_index("ix_provider_operations_step_id")
        batch.drop_index("ix_provider_operations_run_id")
        batch.drop_column("page_id")
        batch.drop_column("step_id")
        batch.drop_column("run_id")
    with op.batch_alter_table("pages") as batch:
        batch.drop_column("recovery_metadata")
    with op.batch_alter_table("workflow_steps") as batch:
        batch.drop_index("ix_workflow_steps_status_retry")
        batch.drop_column("recovery_metadata")
        batch.drop_column("retry_at")
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_index("ix_workflow_runs_status_retry")
        batch.drop_column("recovery_metadata")
        batch.drop_column("retry_at")
