"""Versioned domain contracts shared by workflows, storage, and the API."""

from typing import Annotated, Generic, Literal, TypeAlias, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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


class OutlinePage(ContractModel):
    page_index: int = Field(ge=1, le=50)
    objective: str = Field(min_length=1, max_length=500)
    section: str | None = Field(default=None, max_length=200)


class DeckOutline(ContractModel):
    narrative: str = Field(min_length=1, max_length=2_000)
    pages: tuple[OutlinePage, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_page_indexes(self) -> "DeckOutline":
        indexes = [page.page_index for page in self.pages]
        if indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("outline page indexes must be continuous from 1")
        return self


class DesignSystem(ContractModel):
    color_palette: tuple[str, ...] = Field(min_length=2, max_length=8)
    typography: dict[str, str]
    visual_style: str = Field(min_length=1, max_length=1_000)
    image_style: str = Field(min_length=1, max_length=1_000)
    spacing_scale: tuple[int, ...] = Field(min_length=3, max_length=8)
    prohibited: tuple[str, ...] = ()


class PageSpec(ContractModel):
    page_index: int = Field(ge=1, le=50)
    objective: str = Field(min_length=1, max_length=500)
    page_type: Literal["cover", "section", "content", "data", "closing"]
    title: str | None = Field(default=None, max_length=200)
    body_blocks: tuple[str, ...] = ()
    layout_intent: str | None = Field(default=None, max_length=1_000)
    visual_requirements: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()


class PageSpecs(ContractModel):
    brief_page_count: int = Field(ge=1, le=50)
    outline: DeckOutline
    pages: tuple[PageSpec, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_against_brief_and_outline(self) -> "PageSpecs":
        if len(self.outline.pages) != self.brief_page_count:
            raise ValueError("outline must match brief page count")
        if len(self.pages) != self.brief_page_count:
            raise ValueError("page specs must match brief page count")
        indexes = [page.page_index for page in self.pages]
        if indexes != list(range(1, self.brief_page_count + 1)):
            raise ValueError("page spec indexes must be continuous from 1")
        for outline_page, page in zip(self.outline.pages, self.pages, strict=True):
            if page.objective != outline_page.objective:
                raise ValueError(
                    f"page {page.page_index} objective must match outline objective"
                )
        return self


class PageSpecPatch(ContractModel):
    """Only the pages selected by a targeted revision."""

    pages: tuple[PageSpec, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_unique_indexes(self) -> "PageSpecPatch":
        indexes = [page.page_index for page in self.pages]
        if len(indexes) != len(set(indexes)):
            raise ValueError("page patch indexes must be unique")
        return self


class PlanningBundle(ContractModel):
    brief_artifact_id: str = Field(min_length=1)
    outline_artifact_id: str = Field(min_length=1)
    design_system_artifact_id: str = Field(min_length=1)
    page_spec_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=50)


class ClarificationQuestion(ContractModel):
    field: Literal[
        "topic",
        "purpose",
        "audience",
        "language",
        "tone",
        "page_count",
        "budget_minor_units",
        "quality",
    ]
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    input_type: Literal["single_select", "multi_select", "text", "number"]
    options: tuple[str, ...] = ()
    recommended_value: str | int | tuple[str, ...] | None = None
    required: bool = True


class BriefReady(ContractModel):
    kind: Literal["ready"] = "ready"
    brief: PresentationBrief


class ClarificationRequired(ContractModel):
    kind: Literal["clarification_required"] = "clarification_required"
    reason: str = Field(min_length=1, max_length=1_000)
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1, max_length=4)


class BriefDecision(ContractModel):
    """Runtime JSON-schema shape for the discriminated brief decision."""

    kind: Literal["ready", "clarification_required"]
    brief: PresentationBrief | None = None
    reason: str | None = Field(default=None, max_length=1_000)
    questions: tuple[ClarificationQuestion, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def validate_decision(self) -> "BriefDecision":
        if self.kind == "ready":
            if self.brief is None or self.reason is not None or self.questions:
                raise ValueError("ready decision requires only a brief")
        elif (
            self.brief is not None
            or not self.reason
            or not 1 <= len(self.questions) <= 4
        ):
            raise ValueError(
                "clarification_required needs a reason and one to four questions"
            )
        return self


ClarificationDecision: TypeAlias = Annotated[
    BriefReady | ClarificationRequired,
    Field(discriminator="kind"),
]


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
