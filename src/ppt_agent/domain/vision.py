"""Strict contracts for vision, OCR, quality, and page editing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VisionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class NormalizedBoxV1(VisionContract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "NormalizedBoxV1":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("normalized box must fit inside the unit canvas")
        return self


class VisionRegionV1(VisionContract):
    label: str = Field(min_length=1, max_length=200)
    box: NormalizedBoxV1
    confidence: float = Field(ge=0, le=1)


class VisionAnalysisV1(VisionContract):
    schema_version: Literal[1] = 1
    summary: str = Field(min_length=1, max_length=4_000)
    regions: tuple[VisionRegionV1, ...] = Field(default=(), max_length=100)
    contains_generated_text: bool = False
    safe_area_intrusion: bool = False


class OCRSpanV1(VisionContract):
    text: str = Field(min_length=1, max_length=2_000)
    box: NormalizedBoxV1
    confidence: float | None = Field(default=None, ge=0, le=1)


class OCRReportV1(VisionContract):
    schema_version: Literal[1] = 1
    full_text: str = Field(default="", max_length=100_000)
    spans: tuple[OCRSpanV1, ...] = Field(default=(), max_length=10_000)


class QualityIssueV1(VisionContract):
    code: Literal[
        "layout_overflow",
        "unsafe_overlap",
        "low_contrast",
        "blank_page",
        "text_missing",
        "text_mismatch",
        "generated_text",
        "safe_area_intrusion",
        "artifact_invalid",
    ]
    severity: Literal["warning", "error"]
    element_id: str | None = Field(default=None, max_length=100)
    summary: str = Field(min_length=1, max_length=500)


class QualityReportV1(VisionContract):
    schema_version: Literal[1] = 1
    page_id: str = Field(min_length=1, max_length=100)
    passed: bool
    issues: tuple[QualityIssueV1, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def validate_passed(self) -> "QualityReportV1":
        has_error = any(issue.severity == "error" for issue in self.issues)
        if self.passed == has_error:
            raise ValueError("passed must be false exactly when an error issue exists")
        return self


class PageEditRequestV1(VisionContract):
    schema_version: Literal[1] = 1
    instruction: str = Field(min_length=1, max_length=2_000)
    resample_visual: bool = False


class PageEditPlanV1(VisionContract):
    schema_version: Literal[1] = 1
    kind: Literal[
        "content_edit", "style_edit", "visual_edit", "full_page_regeneration", "ambiguous"
    ]
    instruction: str = Field(min_length=1, max_length=2_000)
    target_element_ids: tuple[str, ...] = Field(default=(), max_length=50)
    target_box: NormalizedBoxV1 | None = None
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "PageEditPlanV1":
        if self.kind == "visual_edit" and self.target_box is None:
            raise ValueError("visual_edit requires target_box")
        if self.kind == "ambiguous" and not self.clarification_question:
            raise ValueError("ambiguous edit requires clarification_question")
        if self.kind != "ambiguous" and self.clarification_question is not None:
            raise ValueError("only ambiguous edits may ask a clarification question")
        return self

