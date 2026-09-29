"""Add production, vision, editing, and export persistence.

Revision ID: 0010_production_backend
Revises: 0009_source_materials
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010_production_backend"
down_revision: str | None = "0009_source_materials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIMESTAMPS = (
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
)


def upgrade() -> None:
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("artifact_references", naming_convention=naming) as batch:
        batch.drop_constraint(
            "fk_artifact_references_artifact_id_artifacts", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_artifact_reference_artifact_project",
            "artifacts",
            ["artifact_id", "project_id"],
            ["id", "project_id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "provider_binary_results",
        sa.Column("operation_id", sa.String(100), primary_key=True),
        sa.Column("attempt_id", sa.String(100), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(255), nullable=False),
        sa.Column("result_kind", sa.String(20), nullable=False),
        sa.Column("result_value", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("download_status", sa.String(30), nullable=False),
        sa.Column("artifact_id", sa.String(36)),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["operation_id"], ["provider_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["provider_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_provider_binary_results_artifact_id", "provider_binary_results", ["artifact_id"])
    op.create_table(
        "provider_vision_results",
        sa.Column("operation_id", sa.String(100), primary_key=True),
        sa.Column("attempt_id", sa.String(100), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(255), nullable=False),
        sa.Column("schema_name", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["operation_id"], ["provider_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["provider_attempts.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "production_inputs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("planning_run_id", sa.String(36), nullable=False),
        sa.Column("planning_bundle_artifact_id", sa.String(36), nullable=False),
        sa.Column("production_spec_artifact_id", sa.String(36), nullable=False),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planning_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["planning_bundle_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["production_spec_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_production_inputs_project_id", "production_inputs", ["project_id"])
    op.create_index("ix_production_inputs_planning_run_id", "production_inputs", ["planning_run_id"])
    op.create_table(
        "page_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("production_spec_artifact_id", sa.String(36), nullable=False),
        sa.Column("visual_artifact_id", sa.String(36)),
        sa.Column("render_manifest_artifact_id", sa.String(36)),
        sa.Column("dom_report_artifact_id", sa.String(36)),
        sa.Column("page_image_artifact_id", sa.String(36)),
        sa.Column("quality_artifact_id", sa.String(36)),
        sa.Column("source_generation_id", sa.String(36)),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["production_spec_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["visual_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["render_manifest_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dom_report_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["page_image_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["quality_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_generation_id"], ["page_generations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("page_id", "generation", name="uq_page_generation"),
        sa.CheckConstraint("generation >= 1", name="ck_page_generation_positive"),
    )
    op.create_index("ix_page_generations_page_id", "page_generations", ["page_id"])
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("page_generation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("artifact_id", sa.String(36), nullable=False, unique=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("issue_summary", sa.JSON(), nullable=False),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["page_generation_id"], ["page_generations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(255), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("artifact_id", sa.String(36)),
        sa.Column("forced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("risk_acknowledgement", sa.String(1000)),
        sa.Column("error", sa.JSON()),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "manifest_fingerprint", name="uq_export_manifest"),
    )
    op.create_index("ix_exports_project_id", "exports", ["project_id"])
    op.create_index("ix_exports_run_id", "exports", ["run_id"])
    op.create_table(
        "page_edits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("source_generation_id", sa.String(36), nullable=False),
        sa.Column("result_generation_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("plan_payload", sa.JSON()),
        sa.Column("status", sa.String(30), nullable=False),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_generation_id"], ["page_generations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_generation_id"], ["page_generations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("page_id", "idempotency_key", name="uq_page_edit_idempotency"),
    )
    op.create_index("ix_page_edits_page_id", "page_edits", ["page_id"])
    op.create_table(
        "material_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("material_id", sa.String(36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("parsed_artifact_id", sa.String(36)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["material_id"], ["source_materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parsed_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("material_id", "attempt_no", name="uq_material_attempt_number"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_material_attempt_positive"),
    )
    op.create_index("ix_material_attempts_material_id", "material_attempts", ["material_id"])
    op.create_table(
        "provider_retry_schedules",
        sa.Column("operation_id", sa.String(100), primary_key=True),
        sa.Column("attempt_id", sa.String(100), nullable=False, unique=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["operation_id"], ["provider_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["provider_attempts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_provider_retry_schedules_due_at", "provider_retry_schedules", ["due_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_retry_schedules_due_at", table_name="provider_retry_schedules")
    for table in (
        "provider_retry_schedules", "material_attempts", "page_edits", "exports",
        "quality_reports", "page_generations", "production_inputs",
        "provider_vision_results", "provider_binary_results",
    ):
        op.drop_table(table)
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("artifact_references", naming_convention=naming) as batch:
        batch.drop_constraint("fk_artifact_reference_artifact_project", type_="foreignkey")
        batch.create_foreign_key(
            "fk_artifact_references_artifact_id_artifacts",
            "artifacts",
            ["artifact_id"],
            ["id"],
            ondelete="CASCADE",
        )
