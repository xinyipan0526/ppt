"""Versioned domain contracts shared by workflows, storage, and the API."""

from typing import Annotated, Generic, Literal, TypeAlias, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Strict base model for data that crosses a system boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


PayloadT = TypeVar("PayloadT")


class SchemaEnvelope(ContractModel, Generic[PayloadT]):
    """Attach an explicit identity and version to a structured payload."""

    schema_name: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    payload: PayloadT


class PresentationBrief(ContractModel):
    """User-confirmed intent used as the root of presentation planning."""

    topic: str = Field(min_length=1)
    purpose: str | None = None
    audience: str | None = None
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    tone: str | None = None
    page_count: int = Field(default=8, ge=1, le=50)
    aspect_ratio: Literal["16:9"] = "16:9"
    budget_minor_units: int | None = Field(default=None, ge=0)
    quality: Literal["draft", "balanced", "high"] = "balanced"


class Completed(ContractModel):
    kind: Literal["completed"] = "completed"
    output_refs: tuple[str, ...] = ()


class Interrupted(ContractModel):
    kind: Literal["interrupted"] = "interrupted"
    interrupt_id: str = Field(min_length=1)


class RetryScheduled(ContractModel):
    kind: Literal["retry_scheduled"] = "retry_scheduled"
    retry_at: AwareDatetime
    reason: str = Field(min_length=1)


class RecoveryRequired(ContractModel):
    kind: Literal["recovery_required"] = "recovery_required"
    reason: str = Field(min_length=1)


class Failed(ContractModel):
    kind: Literal["failed"] = "failed"
    error_code: str = Field(min_length=1)
    recoverable: bool


class Cancelled(ContractModel):
    kind: Literal["cancelled"] = "cancelled"
    reason: str = Field(min_length=1)


StepResult: TypeAlias = Annotated[
    Completed
    | Interrupted
    | RetryScheduled
    | RecoveryRequired
    | Failed
    | Cancelled,
    Field(discriminator="kind"),
]
