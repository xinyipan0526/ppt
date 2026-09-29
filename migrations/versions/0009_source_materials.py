"""Add durable source material state.

Revision ID: 0009_source_materials
Revises: 0008_provider_text_results
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_source_materials"
down_revision: str | None = "0008_provider_text_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MATERIAL_STATUSES = ("uploaded", "parsing", "ready", "failed", "deleted")
MATERIAL_ERRORS = (
    "unsupported_media_type", "empty_upload", "file_too_large", "project_upload_limit",
    "invalid_utf8", "markdown_too_deep", "external_resource_limit", "pdf_encrypted",
    "pdf_page_limit", "pdf_object_limit", "pdf_malformed", "image_pixel_limit",
    "image_frame_limit", "image_compression_limit", "image_decode_failed",
    "parser_timeout", "parser_crashed", "parser_output_invalid", "parser_interrupted",
    "parsed_artifact_unavailable",
)


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.create_unique_constraint("uq_artifact_id_project", ["id", "project_id"])
    op.create_table(
        "source_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("source_artifact_id", sa.String(36), nullable=False, unique=True),
        sa.Column("parsed_artifact_id", sa.String(36), unique=True),
        sa.Column(
            "status",
            sa.Enum(*MATERIAL_STATUSES, name="material_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("parser_name", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(50), nullable=False),
        sa.Column("limits_json", sa.JSON(), nullable=False),
        sa.Column("limits_fingerprint", sa.String(100), nullable=False),
        sa.Column("source_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "error_code",
            sa.Enum(
                *MATERIAL_ERRORS,
                name="material_error_code",
                native_enum=False,
                create_constraint=True,
            ),
        ),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_artifact_id", "project_id"], ["artifacts.id", "artifacts.project_id"],
            ondelete="RESTRICT", name="fk_source_material_source_artifact_project",
        ),
        sa.ForeignKeyConstraint(
            ["parsed_artifact_id", "project_id"], ["artifacts.id", "artifacts.project_id"],
            ondelete="RESTRICT", name="fk_source_material_parsed_artifact_project",
        ),
        sa.CheckConstraint("source_bytes >= 0", name="ck_source_material_bytes_nonnegative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_source_material_attempt_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_source_material_version_positive"),
        sa.CheckConstraint(
            "(status = 'ready' AND parsed_artifact_id IS NOT NULL AND error_code IS NULL) "
            "OR (status = 'failed' AND parsed_artifact_id IS NULL AND error_code IS NOT NULL) "
            "OR (status IN ('uploaded', 'parsing', 'deleted') "
            "AND parsed_artifact_id IS NULL AND error_code IS NULL)",
            name="ck_source_material_state_payload",
        ),
    )
    op.create_index("ix_source_materials_project_id", "source_materials", ["project_id"])
    op.create_index(
        "ix_source_material_project_status", "source_materials", ["project_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_source_material_project_status", table_name="source_materials")
    op.drop_index("ix_source_materials_project_id", table_name="source_materials")
    op.drop_table("source_materials")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("uq_artifact_id_project", type_="unique")
