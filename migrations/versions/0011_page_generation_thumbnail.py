"""Add durable thumbnail reference to page generations.

Revision ID: 0011_page_generation_thumbnail
Revises: 0010_production_backend
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_page_generation_thumbnail"
down_revision = "0010_production_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("page_generations") as batch:
        batch.add_column(sa.Column("thumbnail_artifact_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_page_generations_thumbnail_artifact_id_artifacts",
            "artifacts", ["thumbnail_artifact_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("page_generations") as batch:
        batch.drop_constraint("fk_page_generations_thumbnail_artifact_id_artifacts", type_="foreignkey")
        batch.drop_column("thumbnail_artifact_id")
