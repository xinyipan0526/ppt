"""Versioned export manifests for image-based presentation products."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExportContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExportPageV1(ExportContract):
    page_index: int = Field(ge=1, le=50)
    artifact_id: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(min_length=1, max_length=100)


class ExportManifestV1(ExportContract):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=100)
    production_run_id: str = Field(min_length=1, max_length=100)
    format: Literal["png", "jpg", "png_zip", "jpg_zip", "pdf", "pptx"]
    pages: tuple[ExportPageV1, ...] = Field(min_length=1, max_length=50)
    exporter_version: str = Field(min_length=1, max_length=100)
    jpeg_quality: int = Field(default=92, ge=1, le=100)
    pdf_dpi: int = Field(default=144, ge=72, le=600)
    forced: bool = False
    risk_acknowledgement: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ExportManifestV1":
        indexes = [page.page_index for page in self.pages]
        if indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("export page indexes must be continuous from 1")
        if self.forced and not self.risk_acknowledgement:
            raise ValueError("forced export requires risk acknowledgement")
        if not self.forced and self.risk_acknowledgement is not None:
            raise ValueError("ordinary export cannot include risk acknowledgement")
        if self.format not in {"jpg", "jpg_zip"} and self.jpeg_quality != 92:
            raise ValueError("jpeg_quality is only supported for JPEG exports")
        if self.format != "pdf" and self.pdf_dpi != 144:
            raise ValueError("pdf_dpi is only supported for PDF exports")
        return self

    def identity_payload(self) -> dict:
        """Return the Task-4-compatible payload used for cache identities."""
        payload = self.model_dump(mode="json")
        if self.jpeg_quality == 92:
            payload.pop("jpeg_quality")
        if self.pdf_dpi == 144:
            payload.pop("pdf_dpi")
        return payload
