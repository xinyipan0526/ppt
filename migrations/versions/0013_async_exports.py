"""Make exports durable asynchronous jobs.

Revision ID: 0013_async_exports
Revises: 0012_deck_generations
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_async_exports"
down_revision: str | None = "0012_deck_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0010 used ``stale`` when pages changed during a synchronous export.
    # Normalize those rows before installing the explicit durable-state check.
    op.execute(
        "UPDATE exports SET status = 'failed', "
        "error = '{\"code\":\"pages_changed\"}' WHERE status = 'stale'"
    )
    with op.batch_alter_table("exports") as batch:
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_exports_status",
            "status IN ('queued','running','available','failed','cancelled')",
        )


def downgrade() -> None:
    with op.batch_alter_table("exports") as batch:
        batch.drop_constraint("ck_exports_status", type_="check")
        batch.drop_column("finished_at")
        batch.drop_column("started_at")
        batch.drop_column("attempt_count")
