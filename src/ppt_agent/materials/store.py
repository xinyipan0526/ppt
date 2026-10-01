"""Transactional persistence for source-material parsing state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ppt_agent.artifacts.store import ArtifactSnapshot, ArtifactStore
from ppt_agent.domain.artifacts import ArtifactStatus
from ppt_agent.domain.materials import MaterialErrorCode, MaterialStatus
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ArtifactRecord,
    ArtifactReferenceRecord,
    MaterialAttemptRecord,
    ProjectRecord,
    SourceMaterialRecord,
)


class MaterialError(RuntimeError):
    pass


class MaterialNotFound(MaterialError):
    pass


class MaterialConflict(MaterialError):
    pass


class MaterialQuotaExceeded(MaterialError):
    pass


@dataclass(frozen=True, slots=True)
class MaterialSnapshot:
    id: str
    project_id: str
    display_name: str
    media_type: str
    source_artifact_id: str
    parsed_artifact_id: str | None
    status: MaterialStatus
    parser_name: str
    parser_version: str
    limits: dict[str, Any]
    limits_fingerprint: str
    source_bytes: int
    error_code: MaterialErrorCode | None
    error_detail: str | None
    attempt_count: int
    version: int


@dataclass(frozen=True, slots=True)
class ResolvedMaterial:
    material: MaterialSnapshot
    parsed_artifact: ArtifactSnapshot


@dataclass(frozen=True, slots=True)
class MaterialReconciliationReport:
    recovered: tuple[str, ...]
    failed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialAttemptSnapshot:
    id: str
    material_id: str
    attempt_no: int
    status: str
    error_code: str | None
    error_detail: str | None
    parsed_artifact_id: str | None
    finished_at: datetime | None


class MaterialStore:
    def __init__(
        self,
        sessions: SessionFactory,
        artifacts: ArtifactStore,
        *,
        max_project_bytes: int,
    ) -> None:
        if max_project_bytes <= 0:
            raise ValueError("max_project_bytes must be positive")
        self._sessions = sessions
        self._artifacts = artifacts
        self._max_project_bytes = max_project_bytes

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        session.execute(text("BEGIN IMMEDIATE"))

    def create_uploaded(
        self,
        *,
        project_id: str,
        display_name: str,
        media_type: str,
        source_artifact_id: str,
        source_bytes: int,
        parser_name: str,
        parser_version: str,
        limits: dict[str, Any],
        limits_fingerprint: str,
        material_id: str | None = None,
    ) -> MaterialSnapshot:
        if source_bytes < 0:
            raise ValueError("source_bytes must be non-negative")
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            if session.get(ProjectRecord, project_id) is None:
                raise MaterialNotFound(f"project not found: {project_id}")
            source = session.get(ArtifactRecord, source_artifact_id)
            if source is None or source.project_id != project_id:
                raise MaterialConflict("source artifact must belong to the same project")
            if source.status is not ArtifactStatus.AVAILABLE:
                raise MaterialConflict("source artifact is not available")
            used = session.scalar(
                select(func.coalesce(func.sum(SourceMaterialRecord.source_bytes), 0)).where(
                    SourceMaterialRecord.project_id == project_id,
                    SourceMaterialRecord.status != MaterialStatus.DELETED,
                )
            )
            if int(used or 0) + source_bytes > self._max_project_bytes:
                raise MaterialQuotaExceeded("project upload byte limit exceeded")
            record = SourceMaterialRecord(
                id=material_id or str(uuid4()),
                project_id=project_id,
                display_name=display_name,
                media_type=media_type,
                source_artifact_id=source_artifact_id,
                parsed_artifact_id=None,
                status=MaterialStatus.UPLOADED,
                parser_name=parser_name,
                parser_version=parser_version,
                limits_json=dict(limits),
                limits_fingerprint=limits_fingerprint,
                source_bytes=source_bytes,
                error_code=None,
                error_detail=None,
                attempt_count=0,
                version=1,
            )
            session.add(record)
            session.add(
                ArtifactReferenceRecord(
                    id=str(uuid4()), project_id=project_id, artifact_id=source_artifact_id,
                    owner_type="source_material", owner_id=record.id, role="original",
                )
            )
            session.flush()
            return self._snapshot(record)

    def remaining_upload_bytes(self, project_id: str) -> int:
        """Return a preliminary project allowance; finalization rechecks atomically."""

        with session_scope(self._sessions) as session:
            if session.get(ProjectRecord, project_id) is None:
                raise MaterialNotFound(f"project not found: {project_id}")
            used = session.scalar(
                select(func.coalesce(func.sum(SourceMaterialRecord.source_bytes), 0)).where(
                    SourceMaterialRecord.project_id == project_id,
                    SourceMaterialRecord.status != MaterialStatus.DELETED,
                )
            )
            return max(0, self._max_project_bytes - int(used or 0))

    def claim(self, material_id: str) -> MaterialSnapshot | None:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require(session, material_id)
            if record.status is not MaterialStatus.UPLOADED:
                return None
            record.status = MaterialStatus.PARSING
            record.attempt_count += 1
            self._create_attempt(session, record)
            self._advance(record)
            session.flush()
            return self._snapshot(record)

    def complete(
        self, material_id: str, *, expected_version: int, parsed_artifact_id: str
    ) -> MaterialSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require_version(session, material_id, expected_version)
            if record.status is not MaterialStatus.PARSING:
                raise MaterialConflict("only parsing material can complete")
            parsed = session.get(ArtifactRecord, parsed_artifact_id)
            if parsed is None or parsed.project_id != record.project_id:
                raise MaterialConflict("parsed artifact must belong to the same project")
            if parsed.status is not ArtifactStatus.AVAILABLE or parsed.kind != "parsed_material":
                raise MaterialConflict("parsed artifact is not an available parsed material")
            record.parsed_artifact_id = parsed_artifact_id
            record.status = MaterialStatus.READY
            record.error_code = None
            record.error_detail = None
            self._finish_attempt(session, record, "succeeded", parsed_artifact_id=parsed_artifact_id)
            self._advance(record)
            self._ensure_parsed_reference(session, record)
            session.flush()
            return self._snapshot(record)

    def fail(
        self,
        material_id: str,
        *,
        expected_version: int,
        error_code: MaterialErrorCode,
        safe_detail: str,
    ) -> MaterialSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require_version(session, material_id, expected_version)
            if record.status is not MaterialStatus.PARSING:
                raise MaterialConflict("only parsing material can fail")
            self._set_failed(record, error_code, safe_detail)
            self._finish_attempt(
                session, record, "failed", error_code=error_code.value,
                error_detail=safe_detail,
            )
            session.flush()
            return self._snapshot(record)

    def retry(self, material_id: str, *, expected_version: int) -> MaterialSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require_version(session, material_id, expected_version)
            if record.status is not MaterialStatus.FAILED:
                raise MaterialConflict("only failed material can be retried")
            record.status = MaterialStatus.PARSING
            record.error_code = None
            record.error_detail = None
            record.attempt_count += 1
            self._create_attempt(session, record)
            self._advance(record)
            session.flush()
            return self._snapshot(record)

    def delete(self, material_id: str, *, expected_version: int) -> MaterialSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require_version(session, material_id, expected_version)
            if record.status is MaterialStatus.PARSING:
                raise MaterialConflict("parsing material cannot be deleted")
            if record.status is MaterialStatus.DELETED:
                return self._snapshot(record)
            record.status = MaterialStatus.DELETED
            record.parsed_artifact_id = None
            record.error_code = None
            record.error_detail = None
            self._advance(record)
            session.execute(
                delete(ArtifactReferenceRecord).where(
                    ArtifactReferenceRecord.project_id == record.project_id,
                    ArtifactReferenceRecord.owner_type == "source_material",
                    ArtifactReferenceRecord.owner_id == record.id,
                )
            )
            session.flush()
            return self._snapshot(record)

    def get(self, material_id: str) -> MaterialSnapshot:
        with session_scope(self._sessions) as session:
            return self._snapshot(self._require(session, material_id))

    def list_project(self, project_id: str) -> tuple[MaterialSnapshot, ...]:
        with session_scope(self._sessions) as session:
            records = session.scalars(
                select(SourceMaterialRecord)
                .where(SourceMaterialRecord.project_id == project_id)
                .order_by(SourceMaterialRecord.created_at, SourceMaterialRecord.id)
            )
            return tuple(self._snapshot(record) for record in records)

    def list_attempts(self, material_id: str) -> tuple[MaterialAttemptSnapshot, ...]:
        with session_scope(self._sessions) as session:
            self._require(session, material_id)
            records = session.scalars(
                select(MaterialAttemptRecord)
                .where(MaterialAttemptRecord.material_id == material_id)
                .order_by(MaterialAttemptRecord.attempt_no)
            )
            return tuple(self._attempt_snapshot(record) for record in records)

    def list_pending(self) -> tuple[MaterialSnapshot, ...]:
        with session_scope(self._sessions) as session:
            records = session.scalars(
                select(SourceMaterialRecord)
                .where(
                    SourceMaterialRecord.status.in_(
                        {MaterialStatus.UPLOADED, MaterialStatus.PARSING}
                    )
                )
                .order_by(SourceMaterialRecord.created_at, SourceMaterialRecord.id)
            )
            return tuple(self._snapshot(record) for record in records)

    def resolve_ready(
        self, project_id: str, material_ids: tuple[str, ...]
    ) -> tuple[ResolvedMaterial, ...]:
        if len(material_ids) != len(set(material_ids)):
            raise MaterialConflict("material ids must be unique")
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            resolved: list[ResolvedMaterial] = []
            for material_id in material_ids:
                record = self._require(session, material_id)
                if record.project_id != project_id:
                    raise MaterialNotFound(f"material not found: {material_id}")
                if record.status is not MaterialStatus.READY or not record.parsed_artifact_id:
                    raise MaterialConflict(f"material is not ready: {material_id}")
                artifact = session.get(ArtifactRecord, record.parsed_artifact_id)
                if artifact is None or artifact.status is not ArtifactStatus.AVAILABLE:
                    raise MaterialConflict(f"parsed artifact is unavailable: {material_id}")
                resolved.append(
                    ResolvedMaterial(self._snapshot(record), self._artifact_snapshot(artifact))
                )
            return tuple(resolved)

    def reconcile(self) -> MaterialReconciliationReport:
        recovered: list[str] = []
        failed: list[str] = []
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            records = list(session.scalars(select(SourceMaterialRecord)))
            for record in records:
                if record.status is MaterialStatus.PARSING:
                    candidates = list(
                        session.scalars(
                            select(ArtifactRecord).where(
                                ArtifactRecord.project_id == record.project_id,
                                ArtifactRecord.logical_id
                                == f"parsed-material:{record.id}:{record.attempt_count}",
                                ArtifactRecord.kind == "parsed_material",
                                ArtifactRecord.status == ArtifactStatus.AVAILABLE,
                            )
                        )
                    )
                    if len(candidates) == 1:
                        record.parsed_artifact_id = candidates[0].id
                        record.status = MaterialStatus.READY
                        record.error_code = None
                        record.error_detail = None
                        self._advance(record)
                        self._finish_attempt(
                            session, record, "succeeded",
                            parsed_artifact_id=candidates[0].id,
                        )
                        self._ensure_parsed_reference(session, record)
                        recovered.append(record.id)
                    else:
                        self._set_failed(
                            record, MaterialErrorCode.PARSER_INTERRUPTED,
                            "parser was interrupted before a result was committed",
                        )
                        self._finish_attempt(
                            session, record, "failed",
                            error_code=MaterialErrorCode.PARSER_INTERRUPTED.value,
                            error_detail="parser was interrupted before a result was committed",
                        )
                        failed.append(record.id)
                elif record.status is MaterialStatus.READY:
                    artifact = session.get(ArtifactRecord, record.parsed_artifact_id)
                    if artifact is None or artifact.status is not ArtifactStatus.AVAILABLE:
                        session.execute(
                            delete(ArtifactReferenceRecord).where(
                                ArtifactReferenceRecord.project_id == record.project_id,
                                ArtifactReferenceRecord.owner_type == "source_material",
                                ArtifactReferenceRecord.owner_id == record.id,
                                ArtifactReferenceRecord.role == "parsed",
                            )
                        )
                        record.parsed_artifact_id = None
                        self._set_failed(
                            record, MaterialErrorCode.PARSED_ARTIFACT_UNAVAILABLE,
                            "parsed artifact is unavailable",
                        )
                        failed.append(record.id)
            session.flush()
        return MaterialReconciliationReport(tuple(recovered), tuple(failed))

    @staticmethod
    def _require(session: Session, material_id: str) -> SourceMaterialRecord:
        record = session.get(SourceMaterialRecord, material_id)
        if record is None:
            raise MaterialNotFound(f"material not found: {material_id}")
        return record

    @classmethod
    def _require_version(
        cls, session: Session, material_id: str, expected_version: int
    ) -> SourceMaterialRecord:
        record = cls._require(session, material_id)
        if record.version != expected_version:
            raise MaterialConflict("material version conflict")
        return record

    @staticmethod
    def _advance(record: SourceMaterialRecord) -> None:
        record.version += 1
        record.updated_at = datetime.now(UTC)

    @classmethod
    def _set_failed(
        cls, record: SourceMaterialRecord, code: MaterialErrorCode, detail: str
    ) -> None:
        record.status = MaterialStatus.FAILED
        record.parsed_artifact_id = None
        record.error_code = code
        record.error_detail = detail[:500]
        cls._advance(record)

    @staticmethod
    def _ensure_parsed_reference(session: Session, record: SourceMaterialRecord) -> None:
        assert record.parsed_artifact_id is not None
        existing = session.scalar(
            select(ArtifactReferenceRecord).where(
                ArtifactReferenceRecord.project_id == record.project_id,
                ArtifactReferenceRecord.owner_type == "source_material",
                ArtifactReferenceRecord.owner_id == record.id,
                ArtifactReferenceRecord.role == "parsed",
            )
        )
        if existing is None:
            session.add(
                ArtifactReferenceRecord(
                    id=str(uuid4()), project_id=record.project_id,
                    artifact_id=record.parsed_artifact_id,
                    owner_type="source_material", owner_id=record.id, role="parsed",
                )
            )
        else:
            existing.artifact_id = record.parsed_artifact_id

    @staticmethod
    def _create_attempt(session: Session, record: SourceMaterialRecord) -> None:
        session.add(MaterialAttemptRecord(
            id=str(uuid4()), material_id=record.id, attempt_no=record.attempt_count,
            status="parsing", error_code=None, error_detail=None,
            parsed_artifact_id=None, finished_at=None,
        ))

    @staticmethod
    def _finish_attempt(
        session: Session,
        record: SourceMaterialRecord,
        status: str,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        parsed_artifact_id: str | None = None,
    ) -> None:
        attempt = session.scalar(
            select(MaterialAttemptRecord).where(
                MaterialAttemptRecord.material_id == record.id,
                MaterialAttemptRecord.attempt_no == record.attempt_count,
            )
        )
        if attempt is None:
            # Existing databases can contain a parsing row created before attempt
            # history was introduced. Reconciliation backfills it atomically.
            attempt = MaterialAttemptRecord(
                id=str(uuid4()), material_id=record.id,
                attempt_no=max(record.attempt_count, 1), status=status,
            )
            session.add(attempt)
        attempt.status = status
        attempt.error_code = error_code
        attempt.error_detail = error_detail[:500] if error_detail else None
        attempt.parsed_artifact_id = parsed_artifact_id
        attempt.finished_at = datetime.now(UTC)

    @staticmethod
    def _attempt_snapshot(record: MaterialAttemptRecord) -> MaterialAttemptSnapshot:
        return MaterialAttemptSnapshot(
            id=record.id, material_id=record.material_id,
            attempt_no=record.attempt_no, status=record.status,
            error_code=record.error_code, error_detail=record.error_detail,
            parsed_artifact_id=record.parsed_artifact_id,
            finished_at=record.finished_at,
        )

    @staticmethod
    def _snapshot(record: SourceMaterialRecord) -> MaterialSnapshot:
        return MaterialSnapshot(
            id=record.id, project_id=record.project_id, display_name=record.display_name,
            media_type=record.media_type, source_artifact_id=record.source_artifact_id,
            parsed_artifact_id=record.parsed_artifact_id, status=record.status,
            parser_name=record.parser_name, parser_version=record.parser_version,
            limits=dict(record.limits_json), limits_fingerprint=record.limits_fingerprint,
            source_bytes=record.source_bytes, error_code=record.error_code,
            error_detail=record.error_detail, attempt_count=record.attempt_count,
            version=record.version,
        )

    @staticmethod
    def _artifact_snapshot(record: ArtifactRecord) -> ArtifactSnapshot:
        return ArtifactSnapshot(
            id=record.id, project_id=record.project_id, logical_id=record.logical_id,
            version=record.version, kind=record.kind, status=record.status,
            media_type=record.media_type, extension=record.extension,
            staging_path=record.staging_path, relative_path=record.relative_path,
            content_hash=record.content_hash, size_bytes=record.size_bytes,
            spec_fingerprint=record.spec_fingerprint,
            metadata=dict(record.artifact_metadata), operation_id=record.operation_id,
            invalid_reason=record.invalid_reason,
        )
