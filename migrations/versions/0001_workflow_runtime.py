"""Create workflow runtime tables.

Revision ID: 0001_workflow_runtime
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001_workflow_runtime"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def status_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        created_at(),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_name", sa.String(100), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            status_enum(
                "run_status",
                "queued",
                "running",
                "waiting_for_user",
                "waiting_for_retry",
                "cancel_requested",
                "recovery_required",
                "succeeded",
                "failed",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("brief_version_id", sa.String(36)),
        sa.Column("budget_minor_units", sa.Integer()),
        sa.Column("result_refs", sa.JSON()),
        sa.Column("error", sa.JSON()),
        created_at(),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_key", sa.String(150), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            status_enum(
                "step_status",
                "pending",
                "running",
                "waiting_for_user",
                "waiting_for_retry",
                "recovery_required",
                "cancel_requested",
                "succeeded",
                "failed",
                "cancelled",
                "skipped",
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("input_refs", sa.JSON()),
        sa.Column("output_refs", sa.JSON()),
        sa.Column("error", sa.JSON()),
        created_at(),
        sa.UniqueConstraint(
            "run_id", "step_key", "generation", name="uq_step_run_key_generation"
        ),
    )
    op.create_index("ix_workflow_steps_run_id", "workflow_steps", ["run_id"])
    op.create_table(
        "pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            status_enum(
                "page_status",
                "planned",
                "queued",
                "running",
                "waiting_for_budget",
                "waiting_for_user",
                "recovery_required",
                "cancel_requested",
                "succeeded",
                "failed",
                "cancelled",
                "stale",
            ),
            nullable=False,
        ),
        sa.Column(
            "page_stage",
            status_enum(
                "page_stage", "visual_generation", "rendering", "validation"
            ),
        ),
        sa.Column("page_spec_version_id", sa.String(36)),
        sa.Column("current_artifact_id", sa.String(36)),
        sa.Column("error", sa.JSON()),
        created_at(),
        sa.UniqueConstraint("run_id", "page_index", name="uq_page_run_index"),
    )
    op.create_index("ix_pages_run_id", "pages", ["run_id"])
    op.create_table(
        "workflow_interrupts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(36),
            sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "page_id",
            sa.String(36),
            sa.ForeignKey("pages.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "status",
            status_enum("interrupt_status", "pending", "resolved"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("form_schema", sa.JSON(), nullable=False),
        sa.Column("resume_payload", sa.JSON()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        created_at(),
    )
    op.create_index(
        "ix_workflow_interrupts_run_id", "workflow_interrupts", ["run_id"]
    )
    op.create_index(
        "ix_workflow_interrupts_step_id", "workflow_interrupts", ["step_id"]
    )
    op.create_index(
        "ix_workflow_interrupts_page_id", "workflow_interrupts", ["page_id"]
    )
    op.create_table(
        "workflow_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(150), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        created_at(),
        sa.UniqueConstraint("run_id", "run_seq", name="uq_event_run_sequence"),
    )
    op.create_index(
        "ix_event_run_created", "workflow_events", ["run_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_event_run_created", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index("ix_workflow_interrupts_page_id", table_name="workflow_interrupts")
    op.drop_index("ix_workflow_interrupts_step_id", table_name="workflow_interrupts")
    op.drop_index("ix_workflow_interrupts_run_id", table_name="workflow_interrupts")
    op.drop_table("workflow_interrupts")
    op.drop_index("ix_pages_run_id", table_name="pages")
    op.drop_table("pages")
    op.drop_index("ix_workflow_steps_run_id", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index("ix_workflow_runs_project_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_table("projects")

