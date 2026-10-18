"""Transactional image-based export workflow."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from collections.abc import Callable
from datetime import UTC, datetime
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
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None


class ExportInterrupted(RuntimeError):
    """Internal cooperative stop; the durable record remains recoverable."""


class ExportNotRunnable(RuntimeError):
    pass


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
        jpeg_quality: int = 92,
        pdf_dpi: int = 144,
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
                jpeg_quality=jpeg_quality, pdf_dpi=pdf_dpi,
                forced=forced, risk_acknowledgement=risk_acknowledgement,
            )
            payload = manifest.model_dump(mode="json")
            identity_payload = manifest.identity_payload()
            fingerprint = f"sha256:{sha256(json.dumps(identity_payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
            existing = session.scalar(select(ExportRecord).where(ExportRecord.project_id == project_id, ExportRecord.manifest_fingerprint == fingerprint))
            if existing is not None:
                if existing.status in {"failed", "cancelled"}:
                    existing_manifest = ExportManifestV1.model_validate(existing.manifest)
                    if existing_manifest.identity_payload() != identity_payload:
                        raise ValueError("existing export identity does not match current manifest")
                    existing.status = "queued"
                    existing.artifact_id = None
                    existing.error = None
                    existing.started_at = None
                    existing.finished_at = None
                return existing.id
            export_id = str(uuid4())
            session.add(ExportRecord(
                id=export_id, project_id=project_id, run_id=run_id,
                format=output_format, status="queued", manifest_fingerprint=fingerprint,
                manifest=payload, forced=forced, risk_acknowledgement=risk_acknowledgement,
            ))
            return export_id

    def export(
        self,
        export_id: str,
        *,
        interrupted: Callable[[], bool] | None = None,
    ) -> str:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            if record.status == "available" and record.artifact_id:
                return record.artifact_id
            if record.status in {"failed", "cancelled"}:
                raise ExportNotRunnable("export is terminal")
            manifest = ExportManifestV1.model_validate(record.manifest)
            record.status = "running"
            record.attempt_count += 1
            record.started_at = datetime.now(UTC)
            record.finished_at = None
            record.error = None
        if interrupted is not None and interrupted():
            raise ExportInterrupted("export interrupted")
        if not self._manifest_is_current(export_id, manifest):
            self.fail(export_id, code="pages_changed")
            raise ValueError("pages changed while export was running")
        pages = tuple(self._artifacts.read_bytes(item.artifact_id) for item in manifest.pages)
        render_options: dict[str, int] = {}
        if manifest.format in {"jpg", "jpg_zip"}:
            render_options["jpeg_quality"] = manifest.jpeg_quality
        elif manifest.format == "pdf":
            render_options["pdf_dpi"] = manifest.pdf_dpi
        content, media_type, extension = self._render(
            manifest.format, pages, **render_options
        )
        if interrupted is not None and interrupted():
            raise ExportInterrupted("export interrupted")
        if not self._manifest_is_current(export_id, manifest):
            self.fail(export_id, code="pages_changed")
            raise ValueError("pages changed while export was running")
        artifact = commit_bytes(
            self._artifacts, manifest.project_id, f"exports/{export_id}",
            f"export_{manifest.format}", media_type, extension, content,
            f"export:{sha256(json.dumps(manifest.identity_payload(), separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}",
            source_artifact_ids=tuple(item.artifact_id for item in manifest.pages),
            edge_type=ArtifactEdgeType.COMPOSED_FROM,
        )
        publish_error: RuntimeError | ValueError | None = None
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            if record.status != "running":
                publish_error = ExportNotRunnable("export is terminal")
            elif not self._manifest_matches(session, record, manifest):
                record.status = "failed"
                record.error = {"code": "pages_changed"}
                record.artifact_id = None
                record.finished_at = datetime.now(UTC)
                publish_error = ValueError("pages changed while export was running")
            else:
                record.status = "available"
                record.artifact_id = artifact.id
                record.error = None
                record.finished_at = datetime.now(UTC)
        if publish_error is not None:
            raise publish_error
        return artifact.id

    def recoverable_ids(self) -> tuple[str, ...]:
        with session_scope(self._sessions) as session:
            return tuple(session.scalars(
                select(ExportRecord.id)
                .where(ExportRecord.status.in_(("queued", "running")))
                .order_by(ExportRecord.created_at, ExportRecord.id)
            ))

    def fail(
        self,
        export_id: str,
        *,
        code: str = "export_failed",
        expected_attempt_count: int | None = None,
    ) -> None:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            if expected_attempt_count is not None and (
                record.status != "running"
                or record.attempt_count != expected_attempt_count
            ):
                return
            if record.status not in {"queued", "running"}:
                return
            record.status = "failed"
            record.error = {"code": code}
            record.artifact_id = None
            record.finished_at = datetime.now(UTC)

    def _manifest_is_current(self, export_id: str, manifest: ExportManifestV1) -> bool:
        with session_scope(self._sessions) as session:
            record = session.get(ExportRecord, export_id)
            if record is None or record.status not in {"queued", "running"}:
                return False
            return self._manifest_matches(session, record, manifest)

    @staticmethod
    def _manifest_matches(session, record: ExportRecord, manifest: ExportManifestV1) -> bool:
        if record.project_id != manifest.project_id:
            return False
        current = list(session.scalars(
            select(PageRecord)
            .where(PageRecord.run_id == record.run_id)
            .order_by(PageRecord.page_index)
        ))
        if [page.current_artifact_id for page in current] != [
            page.artifact_id for page in manifest.pages
        ]:
            return False
        for item in manifest.pages:
            artifact = session.get(ArtifactRecord, item.artifact_id)
            if (
                artifact is None
                or artifact.project_id != manifest.project_id
                or artifact.kind != "page_image"
                or artifact.status is not ArtifactStatus.AVAILABLE
                or artifact.content_hash != item.content_hash
            ):
                return False
        return True

    def status(self, export_id: str) -> ExportSnapshot:
        with session_scope(self._sessions) as session:
            record = session.get(ExportRecord, export_id)
            if record is None:
                raise LookupError("export not found")
            return ExportSnapshot(
                record.id, record.project_id, record.run_id, record.format,
                record.status, record.artifact_id, record.error,
                record.attempt_count, record.started_at, record.finished_at,
            )

    @staticmethod
    def _render(
        output_format: str,
        pages: tuple[bytes, ...],
        *,
        jpeg_quality: int = 92,
        pdf_dpi: int = 144,
    ) -> tuple[bytes, str, str]:
        if output_format == "pdf":
            return export_pdf(pages, pdf_dpi=pdf_dpi), "application/pdf", ".pdf"
        if output_format == "pptx":
            return export_pptx(pages), "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"
        if output_format in {"png_zip", "jpg_zip"}:
            image_format = "png" if output_format == "png_zip" else "jpg"
            converted = convert_pages(
                pages, image_format, jpeg_quality=jpeg_quality
            )
            return export_zip(converted, image_format), "application/zip", ".zip"
        if output_format in {"png", "jpg"}:
            if len(pages) != 1:
                raise ValueError("direct image export requires a one-page deck; use a ZIP format")
            converted = convert_pages(
                pages, output_format, jpeg_quality=jpeg_quality
            )
            return converted[0], "image/png" if output_format == "png" else "image/jpeg", f".{output_format}"
        raise ValueError("unsupported export format")
