"""Add immutable deck generation snapshots.

Revision ID: 0012_deck_generations
Revises: 0011_page_generation_thumbnail
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_deck_generations"
down_revision: str | None = "0011_page_generation_thumbnail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deck_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("planning_run_id", sa.String(36), nullable=False),
        sa.Column("production_run_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_generation_id", sa.String(36)),
        sa.Column("instruction", sa.Text()),
        sa.Column("page_generation_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("generation >= 1", name="ck_deck_generation_positive"),
        sa.UniqueConstraint("planning_run_id", "generation", name="uq_deck_generation_lineage"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planning_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["production_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_generation_id"], ["deck_generations.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_deck_generations_project_id", "deck_generations", ["project_id"])
    op.create_index("ix_deck_generations_planning_run_id", "deck_generations", ["planning_run_id"])
    op.create_index("ix_deck_generations_production_run_id", "deck_generations", ["production_run_id"])


def downgrade() -> None:
    op.drop_table("deck_generations")
