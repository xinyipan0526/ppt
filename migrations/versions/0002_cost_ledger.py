"""Create atomic cost ledger tables.

Revision ID: 0002_cost_ledger
Revises: 0001_workflow_runtime
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_cost_ledger"
down_revision: str | None = "0001_workflow_runtime"
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
        "budget_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("hard_limit", sa.Integer(), nullable=False),
        sa.Column("soft_limit", sa.Integer()),
        sa.Column("settled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "active_reserved", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "held_unknown", sa.Integer(), nullable=False, server_default="0"
        ),
        created_at(),
        sa.UniqueConstraint(
            "project_id", "stage", "currency", name="uq_budget_project_stage_currency"
        ),
        sa.CheckConstraint(
            "hard_limit >= 0", name="ck_budget_hard_limit_nonnegative"
        ),
        sa.CheckConstraint(
            "soft_limit IS NULL OR (soft_limit >= 0 AND soft_limit <= hard_limit)",
            name="ck_budget_soft_limit_range",
        ),
        sa.CheckConstraint("settled >= 0", name="ck_budget_settled_nonnegative"),
        sa.CheckConstraint(
            "active_reserved >= 0", name="ck_budget_active_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "held_unknown >= 0", name="ck_budget_held_unknown_nonnegative"
        ),
    )
    op.create_index("ix_budget_accounts_project_id", "budget_accounts", ["project_id"])
    op.create_table(
        "cost_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("budget_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("operation_id", sa.String(100), nullable=False, unique=True),
        sa.Column("amount_reserved", sa.Integer(), nullable=False),
        sa.Column("amount_settled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_deviation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pricing_version", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "settled",
                "released",
                "partially_settled",
                "held_unknown",
                name="reservation_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        created_at(),
        sa.CheckConstraint(
            "amount_reserved > 0", name="ck_reservation_amount_positive"
        ),
        sa.CheckConstraint(
            "amount_settled >= 0", name="ck_reservation_settled_nonnegative"
        ),
    )
    op.create_index(
        "ix_cost_reservations_account_id", "cost_reservations", ["account_id"]
    )
    op.create_index(
        "ix_cost_reservations_project_id", "cost_reservations", ["project_id"]
    )
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("usage_key", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "reservation_id",
            sa.String(36),
            sa.ForeignKey("cost_reservations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(100), nullable=False),
        sa.Column("attempt_id", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dimensions", sa.String(50)),
        sa.Column("estimated_amount", sa.Integer(), nullable=False),
        sa.Column("actual_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pricing_version", sa.String(100), nullable=False),
        sa.Column("raw_units", sa.JSON()),
        created_at(),
    )
    op.create_index(
        "ix_usage_records_reservation_id", "usage_records", ["reservation_id"]
    )
    op.create_index("ix_usage_records_operation_id", "usage_records", ["operation_id"])
    op.create_index("ix_usage_records_attempt_id", "usage_records", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_records_attempt_id", table_name="usage_records")
    op.drop_index("ix_usage_records_operation_id", table_name="usage_records")
    op.drop_index("ix_usage_records_reservation_id", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_cost_reservations_project_id", table_name="cost_reservations")
    op.drop_index("ix_cost_reservations_account_id", table_name="cost_reservations")
    op.drop_table("cost_reservations")
    op.drop_index("ix_budget_accounts_project_id", table_name="budget_accounts")
    op.drop_table("budget_accounts")

