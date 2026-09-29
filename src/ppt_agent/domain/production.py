"""Versioned contracts for deterministic page production and rendering."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CanvasV1(ProductionContract):
    width: Literal[1920] = 1920
    height: Literal[1080] = 1080


class SafeAreaV1(ProductionContract):
    top: int = Field(default=64, ge=0, le=540)
    right: int = Field(default=72, ge=0, le=960)
    bottom: int = Field(default=56, ge=0, le=540)
    left: int = Field(default=72, ge=0, le=960)


class BoxV1(ProductionContract):
    x: int = Field(ge=0, lt=1920)
    y: int = Field(ge=0, lt=1080)
    width: int = Field(gt=0, le=1920)
    height: int = Field(gt=0, le=1080)

    @model_validator(mode="after")
    def validate_canvas_bounds(self) -> "BoxV1":
        if self.x + self.width > 1920 or self.y + self.height > 1080:
            raise ValueError("box must fit within the 1920 x 1080 canvas")
        return self


class TypographyV1(ProductionContract):
    font_family_token: Literal["heading", "body", "mono", "symbol"]
    font_size: int = Field(ge=8, le=240)
    min_font_size: int = Field(ge=8, le=240)
    line_height: float = Field(ge=0.8, le=3.0)
    font_weight: int = Field(ge=100, le=900, multiple_of=100)

    @model_validator(mode="after")
    def validate_minimum_size(self) -> "TypographyV1":
        if self.min_font_size > self.font_size:
            raise ValueError("min_font_size must not exceed font_size")
        return self


class TextBlockV1(ProductionContract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    role: Literal["title", "subtitle", "body", "number", "quote", "caption"]
    text: str = Field(min_length=1, max_length=20_000)
    box: BoxV1
    typography: TypographyV1
    overflow: Literal["shrink_then_reflow", "reflow", "error"] = "shrink_then_reflow"
    max_lines: int = Field(ge=1, le=100)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    align: Literal["left", "center", "right"] = "left"
    allow_truncation: bool = False


class VisualRegionV1(ProductionContract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    box: BoxV1
    kind: Literal["generated_image", "source_image", "solid", "none"]
    prompt: str | None = Field(default=None, max_length=4_000)
    source_artifact_ids: tuple[str, ...] = Field(default=(), max_length=14)


class ProductionPageSpecV1(ProductionContract):
    schema_version: Literal[1] = 1
    page_id: str = Field(min_length=1, max_length=100)
    page_index: int = Field(ge=1, le=50)
    render_mode: Literal["hybrid", "deterministic", "full_raster"]
    canvas: CanvasV1 = Field(default_factory=CanvasV1)
    safe_area: SafeAreaV1 = Field(default_factory=SafeAreaV1)
    layout_token: Literal[
        "cover", "title_body", "two_column", "hero_right", "data_focus", "closing"
    ]
    text_blocks: tuple[TextBlockV1, ...] = Field(default=(), max_length=100)
    visual_regions: tuple[VisualRegionV1, ...] = Field(default=(), max_length=20)
    source_artifact_ids: tuple[str, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def validate_page(self) -> "ProductionPageSpecV1":
        if self.canvas.width != 1920 or self.canvas.height != 1080:
            raise ValueError("production canvas must be 1920 x 1080")
        left = self.safe_area.left
        top = self.safe_area.top
        right = self.canvas.width - self.safe_area.right
        bottom = self.canvas.height - self.safe_area.bottom
        for block in self.text_blocks:
            box = block.box
            if (
                box.x < left
                or box.y < top
                or box.x + box.width > right
                or box.y + box.height > bottom
            ):
                raise ValueError(f"text block {block.id} must fit within the safe area")
        if self.render_mode == "full_raster" and self.text_blocks:
            raise ValueError("full_raster pages cannot contain deterministic text blocks")
        return self


class ProductionSpecV1(ProductionContract):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=100)
    planning_run_id: str = Field(min_length=1, max_length=100)
    planning_bundle_artifact_id: str = Field(min_length=1, max_length=100)
    design_system_artifact_id: str = Field(min_length=1, max_length=100)
    pages: tuple[ProductionPageSpecV1, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_page_order(self) -> "ProductionSpecV1":
        indexes = [page.page_index for page in self.pages]
        if indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("production page indexes must be continuous from 1")
        if len({page.page_id for page in self.pages}) != len(self.pages):
            raise ValueError("production page ids must be unique")
        return self


class VisualRequestV1(ProductionContract):
    schema_version: Literal[1] = 1
    prompt: str = Field(min_length=1, max_length=4_000)
    width: int = Field(default=1920, ge=16, le=8192)
    height: int = Field(default=1080, ge=16, le=8192)
    reference_artifact_ids: tuple[str, ...] = Field(default=(), max_length=14)
    edit_instruction: str | None = Field(default=None, max_length=2_000)


class RenderManifestV1(ProductionContract):
    schema_version: Literal[1] = 1
    page: ProductionPageSpecV1
    visual_artifact_ids: tuple[str, ...] = Field(default=(), max_length=20)
    renderer_version: str = Field(min_length=1, max_length=100)
    chromium_version: str = Field(min_length=1, max_length=100)
    font_fingerprint: str = Field(min_length=1, max_length=200)


class LayoutElementReportV1(ProductionContract):
    element_id: str = Field(min_length=1, max_length=100)
    box: BoxV1
    font_size: float | None = Field(default=None, ge=1, le=300)
    overflowed: bool = False


class DOMLayoutReportV1(ProductionContract):
    schema_version: Literal[1] = 1
    page_id: str = Field(min_length=1, max_length=100)
    elements: tuple[LayoutElementReportV1, ...] = Field(default=(), max_length=200)
    network_requests: int = Field(default=0, ge=0)

