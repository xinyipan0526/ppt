"""Create provider operations and support per-attempt reservations.

Revision ID: 0003_provider_operations
Revises: 0002_cost_ledger
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


revision: str = "0003_provider_operations"
down_revision: str | None = "0002_cost_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )


def reservation_columns(*, operation_unique: bool) -> list[Any]:
    constraints: list[Any] = [
        sa.CheckConstraint(
            "amount_reserved > 0", name="ck_reservation_amount_positive"
        ),
        sa.CheckConstraint(
            "amount_settled >= 0", name="ck_reservation_settled_nonnegative"
        ),
    ]
    if operation_unique:
        constraints.append(
            sa.UniqueConstraint("operation_id", name="uq_cost_reservation_operation")
        )
    else:
        constraints.append(
            sa.UniqueConstraint("reservation_key", name="uq_cost_reservation_key")
        )
    return [
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
        sa.Column("operation_id", sa.String(100), nullable=False),
        *(
            [sa.Column("reservation_key", sa.String(150), nullable=False)]
            if not operation_unique
            else []
        ),
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
        *constraints,
    ]


def rebuild_reservations_for_attempts() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    op.create_table(
        "_cost_reservations_v2",
        *reservation_columns(operation_unique=False),
    )
    op.execute(
        """
        INSERT INTO _cost_reservations_v2 (
            id, account_id, project_id, stage, operation_id, reservation_key,
            amount_reserved, amount_settled, budget_deviation, currency,
            pricing_version, status, expires_at, created_at
        )
        SELECT
            id, account_id, project_id, stage, operation_id, operation_id,
            amount_reserved, amount_settled, budget_deviation, currency,
            pricing_version, status, expires_at, created_at
        FROM cost_reservations
        """
    )
    op.drop_table("cost_reservations")
    op.rename_table("_cost_reservations_v2", "cost_reservations")
    op.create_index(
        "ix_cost_reservations_account_id", "cost_reservations", ["account_id"]
    )
    op.create_index(
        "ix_cost_reservations_project_id", "cost_reservations", ["project_id"]
    )
    op.create_index(
        "ix_cost_reservations_operation_id", "cost_reservations", ["operation_id"]
    )
    op.execute("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    rebuild_reservations_for_attempts()
    op.create_table(
        "provider_operations",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("operation_kind", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column(
            "aggregate_status",
            sa.Enum(
                "pending",
                "in_progress",
                "succeeded",
                "failed",
                "outcome_unknown",
                "cancelled",
                name="operation_aggregate_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("selected_attempt_id", sa.String(100)),
        sa.Column(
            "has_unknown_attempts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        created_at(),
    )
    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column(
            "operation_id",
            sa.String(100),
            sa.ForeignKey("provider_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "prepared",
                "submitted",
                "provider_running",
                "reconciling",
                "cancel_requested",
                "succeeded",
                "failed",
                "outcome_unknown",
                "cancelled",
                name="provider_attempt_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("provider_job_id", sa.String(255)),
        sa.Column(
            "reservation_id",
            sa.String(36),
            sa.ForeignKey("cost_reservations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_started_at", sa.DateTime(timezone=True)),
        sa.Column("response_received_at", sa.DateTime(timezone=True)),
        sa.Column(
            "usage_record_id",
            sa.String(36),
            sa.ForeignKey("usage_records.id", ondelete="SET NULL"),
        ),
        sa.Column("error_code", sa.String(100)),
        sa.Column("response_summary", sa.JSON()),
        created_at(),
        sa.UniqueConstraint(
            "operation_id", "attempt_no", name="uq_attempt_operation_number"
        ),
        sa.UniqueConstraint("reservation_id", name="uq_attempt_reservation"),
    )
    op.create_index(
        "ix_provider_attempts_operation_id", "provider_attempts", ["operation_id"]
    )
    op.create_index(
        "ix_provider_attempts_provider_job_id",
        "provider_attempts",
        ["provider_job_id"],
    )
    op.create_table(
        "provider_retry_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "operation_id",
            sa.String(100),
            sa.ForeignKey("provider_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "unknown_attempt_id",
            sa.String(100),
            sa.ForeignKey("provider_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("acknowledgement", sa.String(1000), nullable=False),
        sa.Column("consumed_by_attempt_id", sa.String(100)),
        created_at(),
        sa.UniqueConstraint(
            "operation_id",
            "unknown_attempt_id",
            name="uq_retry_authorization_unknown_attempt",
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_retry_authorizations")
    op.drop_index("ix_provider_attempts_provider_job_id", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_operation_id", table_name="provider_attempts")
    op.drop_table("provider_attempts")
    op.drop_table("provider_operations")

    op.execute("PRAGMA foreign_keys=OFF")
    op.create_table(
        "_cost_reservations_v1",
        *reservation_columns(operation_unique=True),
    )
    op.execute(
        """
        INSERT INTO _cost_reservations_v1 (
            id, account_id, project_id, stage, operation_id,
            amount_reserved, amount_settled, budget_deviation, currency,
            pricing_version, status, expires_at, created_at
        )
        SELECT
            id, account_id, project_id, stage, operation_id,
            amount_reserved, amount_settled, budget_deviation, currency,
            pricing_version, status, expires_at, created_at
        FROM cost_reservations
        """
    )
    op.drop_table("cost_reservations")
    op.rename_table("_cost_reservations_v1", "cost_reservations")
    op.create_index(
        "ix_cost_reservations_account_id", "cost_reservations", ["account_id"]
    )
    op.create_index(
        "ix_cost_reservations_project_id", "cost_reservations", ["project_id"]
    )
    op.execute("PRAGMA foreign_keys=ON")
