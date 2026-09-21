"""Relational records for the workflow runtime foundation."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ppt_agent.domain.state_machine import PageStatus, RunStatus, StepStatus
from ppt_agent.domain.costs import ReservationStatus
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.domain.state_machine import AttemptStatus


class Base(DeclarativeBase):
    """Declarative metadata root used by Alembic."""


class InterruptStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class PageStage(StrEnum):
    VISUAL_GENERATION = "visual_generation"
    RENDERING = "rendering"
    VALIDATION = "validation"


def enum_type(enum_class: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        name=name,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class ProjectRecord(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class WorkflowRunRecord(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_name: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus, "run_status"), nullable=False
    )
    brief_version_id: Mapped[str | None] = mapped_column(String(36))
    budget_minor_units: Mapped[int | None] = mapped_column(Integer)
    result_refs: Mapped[list[str] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkflowStepRecord(TimestampMixin, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "step_key", "generation", name="uq_step_run_key_generation"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_key: Mapped[str] = mapped_column(String(150), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[StepStatus] = mapped_column(
        enum_type(StepStatus, "step_status"), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    input_refs: Mapped[list[str] | None] = mapped_column(JSON)
    output_refs: Mapped[list[str] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PageRecord(TimestampMixin, Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("run_id", "page_index", name="uq_page_run_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PageStatus] = mapped_column(
        enum_type(PageStatus, "page_status"), nullable=False
    )
    page_stage: Mapped[PageStage | None] = mapped_column(
        enum_type(PageStage, "page_stage")
    )
    page_spec_version_id: Mapped[str | None] = mapped_column(String(36))
    current_artifact_id: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkflowInterruptRecord(TimestampMixin, Base):
    __tablename__ = "workflow_interrupts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[InterruptStatus] = mapped_column(
        enum_type(InterruptStatus, "interrupt_status"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    form_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resume_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("run_id", "run_seq", name="uq_event_run_sequence"),
        Index("ix_event_run_created", "run_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class BudgetAccountRecord(TimestampMixin, Base):
    __tablename__ = "budget_accounts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "stage", "currency", name="uq_budget_project_stage_currency"
        ),
        CheckConstraint("hard_limit >= 0", name="ck_budget_hard_limit_nonnegative"),
        CheckConstraint(
            "soft_limit IS NULL OR (soft_limit >= 0 AND soft_limit <= hard_limit)",
            name="ck_budget_soft_limit_range",
        ),
        CheckConstraint("settled >= 0", name="ck_budget_settled_nonnegative"),
        CheckConstraint(
            "active_reserved >= 0", name="ck_budget_active_reserved_nonnegative"
        ),
        CheckConstraint(
            "held_unknown >= 0", name="ck_budget_held_unknown_nonnegative"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    hard_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_limit: Mapped[int | None] = mapped_column(Integer)
    settled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    held_unknown: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CostReservationRecord(TimestampMixin, Base):
    __tablename__ = "cost_reservations"
    __table_args__ = (
        CheckConstraint(
            "amount_reserved > 0", name="ck_reservation_amount_positive"
        ),
        CheckConstraint(
            "amount_settled >= 0", name="ck_reservation_settled_nonnegative"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("budget_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reservation_key: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    amount_reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_settled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        enum_type(ReservationStatus, "reservation_status"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(TimestampMixin, Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("cost_reservations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    attempt_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimensions: Mapped[str | None] = mapped_column(String(50))
    estimated_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_units: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ProviderOperationRecord(TimestampMixin, Base):
    __tablename__ = "provider_operations"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    aggregate_status: Mapped[OperationAggregateStatus] = mapped_column(
        enum_type(OperationAggregateStatus, "operation_aggregate_status"),
        nullable=False,
    )
    selected_attempt_id: Mapped[str | None] = mapped_column(String(100))
    has_unknown_attempts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class ProviderAttemptRecord(TimestampMixin, Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "attempt_no", name="uq_attempt_operation_number"
        ),
        UniqueConstraint("reservation_id", name="uq_attempt_reservation"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_type(AttemptStatus, "provider_attempt_status"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    provider_job_id: Mapped[str | None] = mapped_column(String(255), index=True)
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("cost_reservations.id", ondelete="RESTRICT"), nullable=False
    )
    request_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    response_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    usage_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_records.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    response_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ProviderRetryAuthorizationRecord(TimestampMixin, Base):
    __tablename__ = "provider_retry_authorizations"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "unknown_attempt_id",
            name="uq_retry_authorization_unknown_attempt",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"), nullable=False
    )
    unknown_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("provider_attempts.id", ondelete="CASCADE"), nullable=False
    )
    acknowledgement: Mapped[str] = mapped_column(String(1000), nullable=False)
    consumed_by_attempt_id: Mapped[str | None] = mapped_column(String(100))
