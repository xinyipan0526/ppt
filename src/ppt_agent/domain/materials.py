"""Versioned contracts for untrusted source materials."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ppt_agent.domain.vision import VisionAnalysisV1


class MaterialStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class MaterialErrorCode(StrEnum):
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    EMPTY_UPLOAD = "empty_upload"
    FILE_TOO_LARGE = "file_too_large"
    PROJECT_UPLOAD_LIMIT = "project_upload_limit"
    INVALID_UTF8 = "invalid_utf8"
    MARKDOWN_TOO_DEEP = "markdown_too_deep"
    EXTERNAL_RESOURCE_LIMIT = "external_resource_limit"
    PDF_ENCRYPTED = "pdf_encrypted"
    PDF_PAGE_LIMIT = "pdf_page_limit"
    PDF_OBJECT_LIMIT = "pdf_object_limit"
    PDF_MALFORMED = "pdf_malformed"
    IMAGE_PIXEL_LIMIT = "image_pixel_limit"
    IMAGE_FRAME_LIMIT = "image_frame_limit"
    IMAGE_COMPRESSION_LIMIT = "image_compression_limit"
    IMAGE_DECODE_FAILED = "image_decode_failed"
    PARSER_TIMEOUT = "parser_timeout"
    PARSER_CRASHED = "parser_crashed"
    PARSER_OUTPUT_INVALID = "parser_output_invalid"
    PARSER_INTERRUPTED = "parser_interrupted"
    PARSED_ARTIFACT_UNAVAILABLE = "parsed_artifact_unavailable"


class MaterialContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceLocatorV1(MaterialContract):
    kind: Literal["markdown", "pdf", "image"]
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    scope: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "SourceLocatorV1":
        if self.kind == "markdown":
            if self.start_line is None or self.end_line is None or self.page or self.scope:
                raise ValueError("markdown locator requires only a line span")
            if self.end_line < self.start_line:
                raise ValueError("end_line must not precede start_line")
        elif self.kind == "pdf":
            if self.page is None or self.start_line or self.end_line or self.scope:
                raise ValueError("pdf locator requires only a page")
        elif self.scope is None or self.start_line or self.end_line or self.page:
            raise ValueError("image locator requires only a scope")
        return self


class TextBlockV1(MaterialContract):
    id: str = Field(pattern=r"^block_[0-9a-f]{16,64}$")
    kind: Literal["heading", "paragraph", "list", "table", "caption"]
    text: str = Field(min_length=1, max_length=2_000_000)
    locator: SourceLocatorV1


class ExternalReferenceV1(MaterialContract):
    kind: Literal["link", "image"]
    target: str = Field(min_length=1, max_length=4_096)
    locator: SourceLocatorV1


class DocumentMetadataV1(MaterialContract):
    page_count: int | None = Field(default=None, ge=1, le=200)
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)
    image_format: str | None = Field(default=None, max_length=20)
    image_mode: str | None = Field(default=None, max_length=20)
    frame_count: int | None = Field(default=None, ge=1)


class ParsedMaterialV1(MaterialContract):
    schema_version: Literal[1] = 1
    material_id: str = Field(min_length=1, max_length=100)
    media_type: Literal[
        "text/markdown", "application/pdf", "image/png", "image/jpeg", "image/webp"
    ]
    source_artifact_id: str = Field(min_length=1, max_length=100)
    untrusted_source_content: Literal[True]
    text_blocks: tuple[TextBlockV1, ...] = Field(default=(), max_length=200_000)
    metadata: DocumentMetadataV1 = Field(default_factory=DocumentMetadataV1)
    external_references: tuple[ExternalReferenceV1, ...] = Field(
        default=(), max_length=100
    )
    warnings: tuple[str, ...] = Field(default=(), max_length=1_000)
    visual_analysis: VisionAnalysisV1 | None = None
    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=50)
    limits_fingerprint: str = Field(min_length=1, max_length=100)


def build_text_block_id(
    material_id: str, locator: SourceLocatorV1, text: str
) -> str:
    payload = json.dumps(
        {
            "locator": locator.model_dump(mode="json"),
            "material_id": material_id,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"block_{hashlib.sha256(payload).hexdigest()[:24]}"
