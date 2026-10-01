"""Transactional image-based export workflow."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, text

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.artifacts import ArtifactEdgeType, ArtifactStatus
from ppt_agent.domain.exports import ExportManifestV1, ExportPageV1
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.exports.images import convert_pages
from ppt_agent.exports.pdf import export_pdf
from ppt_agent.exports.pptx import export_pptx
from ppt_agent.exports.zip import export_zip
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ArtifactRecord, ExportRecord, PageGenerationRecord, PageRecord,
    QualityReportRecord, WorkflowRunRecord,
)
from ppt_agent.workflows.artifact_pipeline import commit_bytes


ExportFormat = Literal["png", "jpg", "png_zip", "jpg_zip", "pdf", "pptx"]


@dataclass(frozen=True, slots=True)
class ExportSnapshot:
    id: str
    project_id: str
    run_id: str
    format: str
    status: str
    artifact_id: str | None
    error: dict | None


class ExportWorkflow:
    def __init__(self, sessions: SessionFactory, artifacts: ArtifactStore) -> None:
        self._sessions = sessions
        self._artifacts = artifacts

    def create(
        self,
        project_id: str,
        run_id: str,
        output_format: ExportFormat,
        *,
        forced: bool = False,
        risk_acknowledgement: str | None = None,
    ) -> str:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            run = session.get(WorkflowRunRecord, run_id)
            if run is None or run.project_id != project_id or run.workflow_name != "deck_production":
                raise LookupError("production run not found")
            if run.status is not RunStatus.SUCCEEDED and not forced:
                raise ValueError("production run is not ready for export")
            pages = list(session.scalars(select(PageRecord).where(PageRecord.run_id == run_id).order_by(PageRecord.page_index)))
            if not pages or any(page.status is not PageStatus.SUCCEEDED or not page.current_artifact_id for page in pages):
                raise ValueError("all current pages must be available")
            manifest_pages: list[ExportPageV1] = []
            for page in pages:
                artifact = session.get(ArtifactRecord, page.current_artifact_id)
                generation = session.scalar(
                    select(PageGenerationRecord)
                    .where(PageGenerationRecord.page_id == page.id, PageGenerationRecord.page_image_artifact_id == page.current_artifact_id)
                    .order_by(PageGenerationRecord.generation.desc())
                )
                quality = session.scalar(select(QualityReportRecord).where(QualityReportRecord.page_generation_id == generation.id)) if generation else None
                if artifact is None or artifact.status is not ArtifactStatus.AVAILABLE or not artifact.content_hash:
                    raise ValueError("current page Artifact is unavailable")
                if (quality is None or not quality.passed) and not forced:
                    raise ValueError("current page quality has not passed")
                manifest_pages.append(ExportPageV1(page_index=page.page_index, artifact_id=artifact.id, content_hash=artifact.content_hash))
            manifest = ExportManifestV1(
                project_id=project_id, production_run_id=run_id, format=output_format,
                pages=tuple(manifest_pages), exporter_version="image-export-v1",
                forced=forced, risk_acknowledgement=risk_acknowledgement,
            )
            payload = manifest.model_dump(mode="json")
            fingerprint = f"sha256:{sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
            existing = session.scalar(select(ExportRecord).where(ExportRecord.project_id == project_id, ExportRecord.manifest_fingerprint == fingerprint))
            if existing is not None:
                return existing.id
            export_id = str(uuid4())
            session.add(ExportRecord(
                id=export_id, project_id=project_id, run_id=run_id,
                format=output_format, status="queued", manifest_fingerprint=fingerprint,
                manifest=payload, forced=forced, risk_acknowledgement=risk_acknowledgement,
            ))
            return export_id

    def export(self, export_id: str) -> str:
        with session_scope(self._sessions) as session:
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            if record.status == "available" and record.artifact_id:
                return record.artifact_id
            manifest = ExportManifestV1.model_validate(record.manifest)
            record.status = "running"
        pages = tuple(self._artifacts.read_bytes(item.artifact_id) for item in manifest.pages)
        content, media_type, extension = self._render(manifest.format, pages)
        artifact = commit_bytes(
            self._artifacts, manifest.project_id, f"exports/{export_id}",
            f"export_{manifest.format}", media_type, extension, content,
            f"export:{sha256(manifest.model_dump_json().encode()).hexdigest()}",
            source_artifact_ids=tuple(item.artifact_id for item in manifest.pages),
            edge_type=ArtifactEdgeType.COMPOSED_FROM,
        )
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(ExportRecord, export_id)
            current_pages = list(session.scalars(select(PageRecord).where(PageRecord.run_id == record.run_id).order_by(PageRecord.page_index)))
            if [page.current_artifact_id for page in current_pages] != [page.artifact_id for page in manifest.pages]:
                record.status = "stale"
                record.error = {"code": "pages_changed"}
                raise ValueError("pages changed while export was running")
            record.status = "available"
            record.artifact_id = artifact.id
        return artifact.id

    def status(self, export_id: str) -> ExportSnapshot:
        with session_scope(self._sessions) as session:
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            return ExportSnapshot(record.id, record.project_id, record.run_id, record.format, record.status, record.artifact_id, record.error)

    @staticmethod
    def _render(output_format: str, pages: tuple[bytes, ...]) -> tuple[bytes, str, str]:
        if output_format == "pdf":
            return export_pdf(pages), "application/pdf", ".pdf"
        if output_format == "pptx":
            return export_pptx(pages), "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"
        if output_format in {"png_zip", "jpg_zip"}:
            image_format = "png" if output_format == "png_zip" else "jpg"
            converted = convert_pages(pages, image_format)
            return export_zip(converted, image_format), "application/zip", ".zip"
        if output_format in {"png", "jpg"}:
            if len(pages) != 1:
                raise ValueError("direct image export requires a one-page deck; use a ZIP format")
            converted = convert_pages(pages, output_format)
            return converted[0], "image/png" if output_format == "png" else "image/jpeg", f".{output_format}"
        raise ValueError("unsupported export format")
