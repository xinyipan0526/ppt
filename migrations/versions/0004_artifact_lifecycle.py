"""Add Artifact lifecycle, dependency, reference, and orphan records.

Revision ID: 0004_artifact_lifecycle
Revises: 0003_provider_operations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_artifact_lifecycle"
down_revision: str | None = "0003_provider_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_id", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "staging", "file_committed", "metadata_committed", "available",
                "stale", "superseded", "invalid", "deleted",
                name="artifact_status", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("extension", sa.String(20), nullable=False),
        sa.Column("staging_path", sa.String(500), nullable=False, unique=True),
        sa.Column("relative_path", sa.String(500)),
        sa.Column("content_hash", sa.String(80)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("spec_fingerprint", sa.String(255), nullable=False),
        sa.Column("artifact_metadata", sa.JSON(), nullable=False),
        sa.Column("pending_edges", sa.JSON(), nullable=False),
        sa.Column(
            "operation_id",
            sa.String(100),
            sa.ForeignKey("provider_operations.id", ondelete="SET NULL"),
        ),
        sa.Column("retention_since", sa.DateTime(timezone=True)),
        sa.Column("invalid_reason", sa.String(255)),
        created_at(),
        sa.UniqueConstraint(
            "project_id", "logical_id", "version", name="uq_artifact_logical_version"
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name="ck_artifact_size_nonnegative"
        ),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_content_hash", "artifacts", ["content_hash"])
    op.create_index("ix_artifacts_operation_id", "artifacts", ["operation_id"])
    op.create_index(
        "ix_artifact_cache",
        "artifacts",
        ["project_id", "kind", "spec_fingerprint", "status"],
    )
    op.create_table(
        "artifact_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "edge_type",
            sa.Enum(
                "derived_from", "references", "supersedes", "composed_from", "validates",
                name="artifact_edge_type", native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        created_at(),
        sa.UniqueConstraint(
            "source_artifact_id", "target_artifact_id", "edge_type", name="uq_artifact_edge"
        ),
        sa.CheckConstraint(
            "source_artifact_id <> target_artifact_id", name="ck_artifact_edge_not_self"
        ),
    )
    op.create_index(
        "ix_artifact_edges_source_artifact_id", "artifact_edges", ["source_artifact_id"]
    )
    op.create_index(
        "ix_artifact_edges_target_artifact_id", "artifact_edges", ["target_artifact_id"]
    )
    op.create_table(
        "artifact_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_type", sa.String(50), nullable=False),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        created_at(),
        sa.UniqueConstraint(
            "project_id", "owner_type", "owner_id", "role",
            name="uq_artifact_reference_slot",
        ),
    )
    op.create_index(
        "ix_artifact_references_project_id", "artifact_references", ["project_id"]
    )
    op.create_index(
        "ix_artifact_references_artifact_id", "artifact_references", ["artifact_id"]
    )
    op.create_table(
        "artifact_orphans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("original_path", sa.String(500), nullable=False),
        sa.Column("quarantine_path", sa.String(500), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        created_at(),
    )
    op.create_index(
        "ix_artifact_orphans_original_path", "artifact_orphans", ["original_path"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_orphans_original_path", table_name="artifact_orphans")
    op.drop_table("artifact_orphans")
    op.drop_index("ix_artifact_references_project_id", table_name="artifact_references")
    op.drop_index("ix_artifact_references_artifact_id", table_name="artifact_references")
    op.drop_table("artifact_references")
    op.drop_index("ix_artifact_edges_target_artifact_id", table_name="artifact_edges")
    op.drop_index("ix_artifact_edges_source_artifact_id", table_name="artifact_edges")
    op.drop_table("artifact_edges")
    op.drop_index("ix_artifact_cache", table_name="artifacts")
    op.drop_index("ix_artifacts_operation_id", table_name="artifacts")
    op.drop_index("ix_artifacts_content_hash", table_name="artifacts")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")
