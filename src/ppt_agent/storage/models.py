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
    ForeignKeyConstraint,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ppt_agent.domain.artifacts import ArtifactEdgeType, ArtifactStatus
from ppt_agent.domain.events import EventDurability
from ppt_agent.domain.state_machine import PageStatus, RunStatus, StepStatus
from ppt_agent.domain.costs import ReservationStatus
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.domain.materials import MaterialErrorCode, MaterialStatus
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
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    next_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


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
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PlanningInputRecord(TimestampMixin, Base):
    __tablename__ = "planning_inputs"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_planning_input_generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text)
    revision_scope: Mapped[str | None] = mapped_column(String(20))
    page_indexes: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    clarification_answers: Mapped[dict[str, Any] | None] = mapped_column(JSON)


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
    recovery_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)


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
        Index("ix_event_run_created", "run_id", "recorded_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    step_id: Mapped[str | None] = mapped_column(String(100), index=True)
    page_id: Mapped[str | None] = mapped_column(String(100), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(100), index=True)
    durability: Mapped[EventDurability] = mapped_column(
        enum_type(EventDurability, "event_durability"),
        nullable=False,
        default=EventDurability.CORE,
        server_default=EventDurability.CORE.value,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


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
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        ForeignKey("pages.id", ondelete="SET NULL"), index=True
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


class ProviderTextResultRecord(TimestampMixin, Base):
    """Validated structured output persisted before an operation is selected."""

    __tablename__ = "provider_text_results"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("provider_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    schema_name: Mapped[str] = mapped_column(String(150), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


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


class ArtifactRecord(TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "logical_id", "version", name="uq_artifact_logical_version"
        ),
        UniqueConstraint("id", "project_id", name="uq_artifact_id_project"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name="ck_artifact_size_nonnegative"
        ),
        Index("ix_artifact_cache", "project_id", "kind", "spec_fingerprint", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        enum_type(ArtifactStatus, "artifact_status"), nullable=False
    )
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    staging_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    relative_path: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(80), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    spec_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pending_edges: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="SET NULL"), index=True
    )
    retention_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalid_reason: Mapped[str | None] = mapped_column(String(255))


class SourceMaterialRecord(TimestampMixin, Base):
    __tablename__ = "source_materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            ondelete="RESTRICT",
            name="fk_source_material_source_artifact_project",
        ),
        ForeignKeyConstraint(
            ["parsed_artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            ondelete="RESTRICT",
            name="fk_source_material_parsed_artifact_project",
        ),
        CheckConstraint("source_bytes >= 0", name="ck_source_material_bytes_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="ck_source_material_attempt_nonnegative"),
        CheckConstraint("version >= 1", name="ck_source_material_version_positive"),
        CheckConstraint(
            "(status = 'ready' AND parsed_artifact_id IS NOT NULL AND error_code IS NULL) "
            "OR (status = 'failed' AND parsed_artifact_id IS NULL AND error_code IS NOT NULL) "
            "OR (status IN ('uploaded', 'parsing', 'deleted') "
            "AND parsed_artifact_id IS NULL AND error_code IS NULL)",
            name="ck_source_material_state_payload",
        ),
        Index("ix_source_material_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    parsed_artifact_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    status: Mapped[MaterialStatus] = mapped_column(
        enum_type(MaterialStatus, "material_status"), nullable=False
    )
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    limits_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    source_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[MaterialErrorCode | None] = mapped_column(
        enum_type(MaterialErrorCode, "material_error_code")
    )
    error_detail: Mapped[str | None] = mapped_column(String(500))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class ArtifactEdgeRecord(TimestampMixin, Base):
    __tablename__ = "artifact_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_artifact_id",
            "target_artifact_id",
            "edge_type",
            name="uq_artifact_edge",
        ),
        CheckConstraint(
            "source_artifact_id <> target_artifact_id", name="ck_artifact_edge_not_self"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_type: Mapped[ArtifactEdgeType] = mapped_column(
        enum_type(ArtifactEdgeType, "artifact_edge_type"), nullable=False
    )


class ArtifactReferenceRecord(TimestampMixin, Base):
    __tablename__ = "artifact_references"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "owner_type",
            "owner_id",
            "role",
            name="uq_artifact_reference_slot",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            ondelete="CASCADE",
            name="fk_artifact_reference_artifact_project",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    owner_type: Mapped[str] = mapped_column(String(50), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)


class ArtifactOrphanRecord(TimestampMixin, Base):
    __tablename__ = "artifact_orphans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    quarantine_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)


class ProviderBinaryResultRecord(TimestampMixin, Base):
    __tablename__ = "provider_binary_results"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("provider_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    result_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    result_value: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    download_status: Mapped[str] = mapped_column(String(30), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), index=True
    )


class ProviderVisionResultRecord(TimestampMixin, Base):
    __tablename__ = "provider_vision_results"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("provider_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProductionInputRecord(TimestampMixin, Base):
    __tablename__ = "production_inputs"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    planning_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    planning_bundle_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    production_spec_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )


class PageGenerationRecord(TimestampMixin, Base):
    __tablename__ = "page_generations"
    __table_args__ = (
        UniqueConstraint("page_id", "generation", name="uq_page_generation"),
        CheckConstraint("generation >= 1", name="ck_page_generation_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    production_spec_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    visual_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    render_manifest_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    dom_report_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    page_image_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    quality_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    source_generation_id: Mapped[str | None] = mapped_column(
        ForeignKey("page_generations.id", ondelete="SET NULL")
    )


class QualityReportRecord(TimestampMixin, Base):
    __tablename__ = "quality_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    page_generation_id: Mapped[str] = mapped_column(
        ForeignKey("page_generations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issue_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ExportRecord(TimestampMixin, Base):
    __tablename__ = "exports"
    __table_args__ = (
        UniqueConstraint("project_id", "manifest_fingerprint", name="uq_export_manifest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    manifest_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_acknowledgement: Mapped[str | None] = mapped_column(String(1000))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PageEditRecord(TimestampMixin, Base):
    __tablename__ = "page_edits"
    __table_args__ = (
        UniqueConstraint("page_id", "idempotency_key", name="uq_page_edit_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_generation_id: Mapped[str] = mapped_column(
        ForeignKey("page_generations.id", ondelete="RESTRICT"), nullable=False
    )
    result_generation_id: Mapped[str | None] = mapped_column(
        ForeignKey("page_generations.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    plan_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), nullable=False)


class MaterialAttemptRecord(TimestampMixin, Base):
    __tablename__ = "material_attempts"
    __table_args__ = (
        UniqueConstraint("material_id", "attempt_no", name="uq_material_attempt_number"),
        CheckConstraint("attempt_no >= 1", name="ck_material_attempt_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    material_id: Mapped[str] = mapped_column(
        ForeignKey("source_materials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    parsed_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderRetryScheduleRecord(TimestampMixin, Base):
    __tablename__ = "provider_retry_schedules"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("provider_operations.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("provider_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
