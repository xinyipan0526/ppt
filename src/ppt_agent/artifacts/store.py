"""Crash-recoverable Artifact file and metadata commit protocol."""

from __future__ import annotations

import hashlib
import os
import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ppt_agent.domain.artifacts import (
    CONTENT_DEPENDENCY_EDGES,
    ArtifactEdgeType,
    ArtifactStatus,
)
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.domain.state_machine import RunStatus
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ArtifactEdgeRecord,
    ArtifactOrphanRecord,
    ArtifactRecord,
    ArtifactReferenceRecord,
    InterruptStatus,
    PageRecord,
    ProjectRecord,
    ProviderOperationRecord,
    WorkflowInterruptRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)
from ppt_agent.storage.workspace import UnsafeWorkspacePath, WorkspacePaths


class ArtifactError(RuntimeError):
    """Base class for deterministic Artifact failures."""


class ArtifactNotFound(ArtifactError):
    pass


class ArtifactConflict(ArtifactError):
    pass


class ArtifactIntegrityError(ArtifactError):
    pass


class ArtifactDependencyNotReady(ArtifactConflict):
    pass


@dataclass(frozen=True, slots=True)
class EdgeInput:
    source_artifact_id: str
    edge_type: ArtifactEdgeType


@dataclass(frozen=True, slots=True)
class CreateArtifact:
    project_id: str
    logical_id: str
    kind: str
    media_type: str
    extension: str
    spec_fingerprint: str
    metadata: dict[str, Any]
    edges: tuple[EdgeInput, ...] = ()
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    id: str
    project_id: str
    logical_id: str
    version: int
    kind: str
    status: ArtifactStatus
    media_type: str
    extension: str
    staging_path: str
    relative_path: str | None
    content_hash: str | None
    size_bytes: int | None
    spec_fingerprint: str
    metadata: dict[str, Any]
    operation_id: str | None
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    recovered: tuple[str, ...]
    invalid: tuple[str, ...]
    orphans: tuple[str, ...]
    cleaned_staging: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrphanSnapshot:
    id: str
    original_path: str
    quarantine_path: str
    size_bytes: int
    content_hash: str


_EXTENSION = re.compile(r"^\.[a-z0-9]{1,10}$")


@dataclass(slots=True)
class _WorkspaceLockEntry:
    lock: Lock
    users: int = 0


_WORKSPACE_LOCKS_GUARD = Lock()
_WORKSPACE_LOCKS: dict[tuple[Path, str], _WorkspaceLockEntry] = {}


class ArtifactStore:
    """Own files below one workspace and coordinate them with SQLite state."""

    def __init__(
        self,
        sessions: SessionFactory,
        workspace: Path,
        *,
        staging_retention: timedelta = timedelta(hours=24),
        file_validator: Callable[[Path, ArtifactSnapshot], None] | None = None,
    ) -> None:
        if staging_retention < timedelta(0):
            raise ValueError("staging_retention must be non-negative")
        self._sessions = sessions
        self._paths = WorkspacePaths(workspace, create=True)
        self._workspace = self._paths.root
        self._staging_retention = staging_retention
        self._file_validator = file_validator
        for relative in (".staging", ".orphans", ".trash", "artifacts"):
            directory = self._resolve(relative)
            directory.mkdir(exist_ok=True)
            self._verify(directory)

    def create_staging(self, request: CreateArtifact) -> ArtifactSnapshot:
        self._validate_request(request)
        artifact_id = str(uuid4())
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            if session.get(ProjectRecord, request.project_id) is None:
                raise ArtifactConflict(f"project not found: {request.project_id}")
            version = (
                session.scalar(
                    select(func.max(ArtifactRecord.version)).where(
                        ArtifactRecord.project_id == request.project_id,
                        ArtifactRecord.logical_id == request.logical_id,
                    )
                )
                or 0
            ) + 1
            record = ArtifactRecord(
                id=artifact_id,
                project_id=request.project_id,
                logical_id=request.logical_id,
                version=version,
                kind=request.kind,
                status=ArtifactStatus.STAGING,
                media_type=request.media_type,
                extension=request.extension,
                staging_path=f".staging/{artifact_id}.part",
                relative_path=None,
                content_hash=None,
                size_bytes=None,
                spec_fingerprint=request.spec_fingerprint,
                artifact_metadata=dict(request.metadata),
                pending_edges=[
                    {
                        "source_artifact_id": edge.source_artifact_id,
                        "edge_type": edge.edge_type.value,
                    }
                    for edge in request.edges
                ],
                operation_id=request.operation_id,
            )
            session.add(record)
            session.flush()
            return self._snapshot(record)

    def write_staging(
        self,
        artifact_id: str,
        chunks: Iterable[bytes],
        *,
        expected_hash: str | None = None,
        durable: bool = True,
    ) -> ArtifactSnapshot:
        with self._artifact_lock(artifact_id):
            return self._write_staging_unlocked(
                artifact_id,
                chunks,
                expected_hash=expected_hash,
                durable=durable,
            )

    def _write_staging_unlocked(
        self,
        artifact_id: str,
        chunks: Iterable[bytes],
        *,
        expected_hash: str | None,
        durable: bool,
    ) -> ArtifactSnapshot:
        snapshot = self.get(artifact_id)
        if snapshot.status is not ArtifactStatus.STAGING or snapshot.content_hash:
            raise ArtifactConflict("artifact is not an empty staging record")
        staging = self._resolve(snapshot.staging_path)
        digest = hashlib.sha256()
        size = 0
        try:
            with staging.open("wb") as output:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact chunks must be bytes")
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
                if durable:
                    os.fsync(output.fileno())
            self._verify(staging)
            content_hash = f"sha256:{digest.hexdigest()}"
            if expected_hash is not None and content_hash != expected_hash:
                raise ArtifactIntegrityError(
                    f"content hash mismatch: expected {expected_hash}, got {content_hash}"
                )
            if self._file_validator is not None:
                self._file_validator(staging, snapshot)
            relative = (
                f"artifacts/{digest.hexdigest()[:2]}/"
                f"{digest.hexdigest()}{snapshot.extension}"
            )
            with session_scope(self._sessions) as session:
                record = self._require(session, artifact_id)
                if record.status is not ArtifactStatus.STAGING or record.content_hash:
                    raise ArtifactConflict("artifact staging state changed while writing")
                record.content_hash = content_hash
                record.size_bytes = size
                record.relative_path = relative
                session.flush()
                return self._snapshot(record)
        except BaseException:
            try:
                self._safe_unlink(staging)
            except ArtifactIntegrityError:
                pass
            raise

    def commit_file(self, artifact_id: str, *, durable: bool = True) -> ArtifactSnapshot:
        with self._artifact_lock(artifact_id):
            return self._commit_file_unlocked(artifact_id, durable=durable)

    def _commit_file_unlocked(
        self, artifact_id: str, *, durable: bool
    ) -> ArtifactSnapshot:
        snapshot = self.get(artifact_id)
        if snapshot.status is ArtifactStatus.FILE_COMMITTED:
            return snapshot
        if snapshot.status is not ArtifactStatus.STAGING:
            raise ArtifactConflict("artifact must be staging before file commit")
        if not snapshot.content_hash or not snapshot.relative_path:
            raise ArtifactConflict("staging file has not been written")
        staging = self._resolve(snapshot.staging_path)
        final = self._resolve(snapshot.relative_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        self._verify(final.parent)
        if final.exists():
            self._verify(final)
            if self._hash_file(final) != snapshot.content_hash:
                raise ArtifactIntegrityError("content-addressed destination has wrong hash")
            self._safe_unlink(staging)
        else:
            try:
                self._verify(staging)
            except FileNotFoundError as exc:
                raise ArtifactIntegrityError("staging file is missing") from exc
            if not staging.is_file():
                raise ArtifactIntegrityError("staging file is missing")
            os.replace(staging, final)
            self._verify(final)
        if durable:
            self._verify(final.parent)
            self._fsync_directory(final.parent)
        with session_scope(self._sessions) as session:
            record = self._require(session, artifact_id)
            if record.status is ArtifactStatus.STAGING:
                record.status = ArtifactStatus.FILE_COMMITTED
            elif record.status is not ArtifactStatus.FILE_COMMITTED:
                raise ArtifactConflict("artifact state changed during file commit")
            session.flush()
            return self._snapshot(record)

    def replace_pending_edges(
        self, artifact_id: str, edges: tuple[EdgeInput, ...]
    ) -> ArtifactSnapshot:
        with session_scope(self._sessions) as session:
            record = self._require(session, artifact_id)
            if record.status not in {ArtifactStatus.STAGING, ArtifactStatus.FILE_COMMITTED}:
                raise ArtifactConflict("dependency declaration is already immutable")
            record.pending_edges = [
                {
                    "source_artifact_id": edge.source_artifact_id,
                    "edge_type": edge.edge_type.value,
                }
                for edge in edges
            ]
            session.flush()
            return self._snapshot(record)

    def commit_metadata(self, artifact_id: str) -> ArtifactSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require(session, artifact_id)
            if record.status is ArtifactStatus.METADATA_COMMITTED:
                return self._snapshot(record)
            if record.status is not ArtifactStatus.FILE_COMMITTED:
                raise ArtifactConflict("file must be committed before metadata")
            edges = self._pending_edges(record)
            self._validate_edges(session, record, edges)
            for edge in edges:
                session.add(
                    ArtifactEdgeRecord(
                        id=str(uuid4()),
                        source_artifact_id=edge.source_artifact_id,
                        target_artifact_id=record.id,
                        edge_type=edge.edge_type,
                    )
                )
            record.status = ArtifactStatus.METADATA_COMMITTED
            session.flush()
            return self._snapshot(record)

    def make_available(self, artifact_id: str) -> ArtifactSnapshot:
        with session_scope(self._sessions) as session:
            record = self._require(session, artifact_id)
            if record.status is ArtifactStatus.AVAILABLE:
                self._verify_record_file(record)
                return self._snapshot(record)
            if record.status is not ArtifactStatus.METADATA_COMMITTED:
                raise ArtifactConflict("metadata must be committed before availability")
            self._verify_record_file(record)
            edges = list(
                session.scalars(
                    select(ArtifactEdgeRecord).where(
                        ArtifactEdgeRecord.target_artifact_id == artifact_id
                    )
                )
            )
            for edge in edges:
                source = self._require(session, edge.source_artifact_id)
                if edge.edge_type is ArtifactEdgeType.SUPERSEDES:
                    if source.logical_id != record.logical_id:
                        raise ArtifactConflict("supersedes requires the same logical_id")
                    if source.version >= record.version:
                        raise ArtifactConflict("supersedes must point to an older version")
                    if source.status in {
                        ArtifactStatus.STAGING,
                        ArtifactStatus.FILE_COMMITTED,
                        ArtifactStatus.METADATA_COMMITTED,
                    }:
                        raise ArtifactDependencyNotReady(
                            f"superseded version {source.id} is not ready"
                        )
                    if source.status is ArtifactStatus.AVAILABLE:
                        source.status = ArtifactStatus.SUPERSEDED
                        source.retention_since = datetime.now(UTC)
                    elif source.status is not ArtifactStatus.SUPERSEDED:
                        raise ArtifactConflict(
                            f"superseded version has invalid state: {source.status.value}"
                        )
                elif source.status in {
                    ArtifactStatus.STAGING,
                    ArtifactStatus.FILE_COMMITTED,
                    ArtifactStatus.METADATA_COMMITTED,
                }:
                    raise ArtifactDependencyNotReady(
                        f"dependency {source.id} is not ready: {source.status.value}"
                    )
                elif source.status is not ArtifactStatus.AVAILABLE:
                    raise ArtifactConflict(
                        f"dependency {source.id} is not available: {source.status.value}"
                    )
            record.status = ArtifactStatus.AVAILABLE
            session.flush()
            return self._snapshot(record)

    def get(self, artifact_id: str) -> ArtifactSnapshot:
        with session_scope(self._sessions) as session:
            return self._snapshot(self._require(session, artifact_id))

    def read_bytes(self, artifact_id: str) -> bytes:
        """Read a committed Artifact after rechecking its workspace boundary."""

        snapshot = self.get(artifact_id)
        if snapshot.status not in {
            ArtifactStatus.AVAILABLE,
            ArtifactStatus.STALE,
            ArtifactStatus.SUPERSEDED,
        }:
            raise ArtifactConflict("artifact is not readable")
        if not snapshot.relative_path:
            raise ArtifactIntegrityError("artifact has no committed path")
        path = self._resolve(snapshot.relative_path)
        self._verify(path)
        if not path.is_file():
            raise ArtifactIntegrityError("artifact file is missing")
        if snapshot.content_hash and self._hash_file(path) != snapshot.content_hash:
            raise ArtifactIntegrityError("artifact content hash does not match metadata")
        return path.read_bytes()

    def find_cache_hit(
        self, project_id: str, kind: str, spec_fingerprint: str
    ) -> ArtifactSnapshot | None:
        with session_scope(self._sessions) as session:
            record = session.scalar(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.project_id == project_id,
                    ArtifactRecord.kind == kind,
                    ArtifactRecord.spec_fingerprint == spec_fingerprint,
                    ArtifactRecord.status == ArtifactStatus.AVAILABLE,
                )
                .order_by(ArtifactRecord.created_at.desc())
            )
            return None if record is None else self._snapshot(record)

    def list_by_operation(self, operation_id: str) -> tuple[ArtifactSnapshot, ...]:
        """Return durable outputs associated with one provider operation."""

        with session_scope(self._sessions) as session:
            records = session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.operation_id == operation_id,
                    ArtifactRecord.status.in_(
                        {
                            ArtifactStatus.AVAILABLE,
                            ArtifactStatus.STALE,
                            ArtifactStatus.SUPERSEDED,
                        }
                    ),
                )
                .order_by(ArtifactRecord.logical_id, ArtifactRecord.version.desc())
            )
            return tuple(self._snapshot(record) for record in records)

    def mark_stale(self, artifact_id: str) -> tuple[str, ...]:
        now = datetime.now(UTC)
        changed: list[str] = []
        with session_scope(self._sessions) as session:
            self._require(session, artifact_id)
            pending = deque([artifact_id])
            seen: set[str] = set()
            while pending:
                current_id = pending.popleft()
                if current_id in seen:
                    continue
                seen.add(current_id)
                current = self._require(session, current_id)
                if current.status is ArtifactStatus.AVAILABLE:
                    current.status = ArtifactStatus.STALE
                    current.retention_since = now
                    changed.append(current_id)
                children = session.scalars(
                    select(ArtifactEdgeRecord.target_artifact_id).where(
                        ArtifactEdgeRecord.source_artifact_id == current_id,
                        ArtifactEdgeRecord.edge_type.in_(CONTENT_DEPENDENCY_EDGES),
                    )
                )
                pending.extend(children)
        return tuple(changed)

    def add_reference(
        self, artifact_id: str, *, owner_type: str, owner_id: str, role: str
    ) -> None:
        if not owner_type or not owner_id or not role:
            raise ValueError("reference identity fields must not be empty")
        with session_scope(self._sessions) as session:
            self._begin_immediate(session)
            record = self._require(session, artifact_id)
            if record.status is not ArtifactStatus.AVAILABLE:
                raise ArtifactConflict("only available Artifact can be referenced")
            existing = session.scalar(
                select(ArtifactReferenceRecord).where(
                    ArtifactReferenceRecord.project_id == record.project_id,
                    ArtifactReferenceRecord.owner_type == owner_type,
                    ArtifactReferenceRecord.owner_id == owner_id,
                    ArtifactReferenceRecord.role == role,
                )
            )
            if existing is None:
                session.add(
                    ArtifactReferenceRecord(
                        id=str(uuid4()),
                        project_id=record.project_id,
                        artifact_id=artifact_id,
                        owner_type=owner_type,
                        owner_id=owner_id,
                        role=role,
                    )
                )
            else:
                existing.artifact_id = artifact_id

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        """Acquire SQLite's write reservation before read-modify-write logic."""

        session.execute(text("BEGIN IMMEDIATE"))

    @contextmanager
    def _artifact_lock(self, artifact_id: str) -> Iterator[None]:
        """Serialize one workspace Artifact across all Store instances."""

        with self._workspace_resource_lock(f"artifact:{artifact_id}"):
            yield

    @contextmanager
    def _workspace_resource_lock(self, resource: str) -> Iterator[None]:
        key = (self._workspace, resource)
        with _WORKSPACE_LOCKS_GUARD:
            entry = _WORKSPACE_LOCKS.setdefault(key, _WorkspaceLockEntry(lock=Lock()))
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with _WORKSPACE_LOCKS_GUARD:
                entry.users -= 1
                if entry.users == 0 and _WORKSPACE_LOCKS.get(key) is entry:
                    del _WORKSPACE_LOCKS[key]

    def remove_reference(
        self, artifact_id: str, *, owner_type: str, owner_id: str, role: str
    ) -> None:
        with session_scope(self._sessions) as session:
            session.execute(
                delete(ArtifactReferenceRecord).where(
                    ArtifactReferenceRecord.artifact_id == artifact_id,
                    ArtifactReferenceRecord.owner_type == owner_type,
                    ArtifactReferenceRecord.owner_id == owner_id,
                    ArtifactReferenceRecord.role == role,
                )
            )

    def collect_garbage(self, *, before: datetime) -> tuple[str, ...]:
        collected: list[str] = []
        with session_scope(self._sessions) as session:
            # Serialize the protection recheck and logical delete decision. Files
            # are moved only after this transaction commits, so a DB rollback can
            # never leave an otherwise-live Artifact without its file.
            self._begin_immediate(session)
            candidates = list(
                session.scalars(
                    select(ArtifactRecord).where(
                        ArtifactRecord.status.in_(
                            {ArtifactStatus.STALE, ArtifactStatus.SUPERSEDED}
                        ),
                        ArtifactRecord.retention_since.is_not(None),
                        ArtifactRecord.retention_since < before,
                    )
                )
            )
            for record in candidates:
                if record.operation_id:
                    operation = session.get(ProviderOperationRecord, record.operation_id)
                    if operation is not None and self._operation_protects_artifacts(
                        operation
                    ):
                        continue
                if self._has_live_workflow_reference(session, record.id):
                    continue
                referenced = session.scalar(
                    select(func.count()).select_from(ArtifactReferenceRecord).where(
                        ArtifactReferenceRecord.artifact_id == record.id
                    )
                )
                downstream = session.scalar(
                    select(func.count())
                    .select_from(ArtifactEdgeRecord)
                    .join(
                        ArtifactRecord,
                        ArtifactRecord.id == ArtifactEdgeRecord.target_artifact_id,
                    )
                    .where(
                        ArtifactEdgeRecord.source_artifact_id == record.id,
                        ArtifactRecord.status != ArtifactStatus.DELETED,
                    )
                )
                if referenced or downstream:
                    continue
                record.status = ArtifactStatus.DELETED
                collected.append(record.id)
            session.flush()
        self._finish_deleted_file_moves()
        return tuple(collected)

    def reconcile(self) -> ReconciliationReport:
        with self._workspace_resource_lock("maintenance:reconcile"):
            return self._reconcile_unlocked()

    def _reconcile_unlocked(self) -> ReconciliationReport:
        recovered: set[str] = set()
        invalid: list[str] = []
        cleaned = self._clean_expired_staging()
        self._finish_deleted_file_moves()
        with session_scope(self._sessions) as session:
            ids = list(
                session.scalars(
                    select(ArtifactRecord.id).where(
                        ArtifactRecord.status != ArtifactStatus.DELETED
                    )
                )
            )
        # First make every recoverable file durable. Dependency order is irrelevant here.
        for artifact_id in ids:
            snapshot = self.get(artifact_id)
            try:
                if snapshot.status is ArtifactStatus.STAGING and snapshot.content_hash:
                    self.commit_file(artifact_id)
                elif snapshot.status in {
                    ArtifactStatus.AVAILABLE,
                    ArtifactStatus.STALE,
                    ArtifactStatus.SUPERSEDED,
                }:
                    self._require_snapshot_file(snapshot)
            except (ArtifactIntegrityError, ArtifactConflict):
                self._mark_invalid(artifact_id, "artifact reconciliation failed")
                invalid.append(artifact_id)

        # Then commit all dependency declarations before evaluating availability.
        for artifact_id in ids:
            if artifact_id in invalid:
                continue
            snapshot = self.get(artifact_id)
            if snapshot.status is ArtifactStatus.FILE_COMMITTED:
                try:
                    self._require_snapshot_file(snapshot)
                    self.commit_metadata(artifact_id)
                except (ArtifactIntegrityError, ArtifactConflict):
                    self._mark_invalid(artifact_id, "artifact reconciliation failed")
                    invalid.append(artifact_id)

        # Finally promote in dependency order. A not-yet-ready upstream is deferred,
        # not mistaken for corruption.
        pending = {
            artifact_id
            for artifact_id in ids
            if artifact_id not in invalid
            and self.get(artifact_id).status is ArtifactStatus.METADATA_COMMITTED
        }
        while pending:
            progressed = False
            for artifact_id in tuple(pending):
                try:
                    self.make_available(artifact_id)
                except ArtifactDependencyNotReady:
                    continue
                except (ArtifactIntegrityError, ArtifactConflict):
                    self._mark_invalid(artifact_id, "artifact reconciliation failed")
                    invalid.append(artifact_id)
                else:
                    recovered.add(artifact_id)
                pending.remove(artifact_id)
                progressed = True
            if not progressed:
                break

        orphans = self._resume_orphan_intents()
        orphans.extend(self._quarantine_orphans())
        return ReconciliationReport(
            tuple(sorted(recovered)), tuple(invalid), tuple(orphans), tuple(cleaned)
        )

    def _clean_expired_staging(self) -> list[str]:
        cutoff = datetime.now(UTC) - self._staging_retention
        cleaned: list[str] = []
        with session_scope(self._sessions) as session:
            records = list(
                session.scalars(
                    select(ArtifactRecord).where(
                        ArtifactRecord.status == ArtifactStatus.STAGING,
                        ArtifactRecord.content_hash.is_(None),
                        ArtifactRecord.created_at <= cutoff,
                    )
                )
            )
            for record in records:
                if record.operation_id:
                    operation = session.get(ProviderOperationRecord, record.operation_id)
                    if operation is not None and self._operation_protects_artifacts(
                        operation
                    ):
                        continue
                self._safe_unlink(self._resolve(record.staging_path))
                record.status = ArtifactStatus.DELETED
                record.retention_since = datetime.now(UTC)
                cleaned.append(record.id)
        return cleaned

    def list_orphans(self) -> tuple[OrphanSnapshot, ...]:
        with session_scope(self._sessions) as session:
            records = list(
                session.scalars(
                    select(ArtifactOrphanRecord).order_by(ArtifactOrphanRecord.created_at)
                )
            )
            return tuple(
                OrphanSnapshot(
                    id=record.id,
                    original_path=record.original_path,
                    quarantine_path=record.quarantine_path,
                    size_bytes=record.size_bytes,
                    content_hash=record.content_hash,
                )
                for record in records
            )

    def _resume_orphan_intents(self) -> list[str]:
        moved: list[str] = []
        with session_scope(self._sessions) as session:
            records = list(session.scalars(select(ArtifactOrphanRecord)))
        for record in records:
            source = self._resolve(record.original_path)
            destination = self._resolve(record.quarantine_path)
            if destination.exists():
                self._verify(destination)
                if self._hash_file(destination) != record.content_hash:
                    raise ArtifactIntegrityError(
                        f"quarantined orphan hash mismatch: {record.quarantine_path}"
                    )
                if source.exists():
                    self._verify(source)
                    if self._hash_file(source) != record.content_hash:
                        raise ArtifactIntegrityError(
                            f"orphan source conflicts with quarantine: {record.original_path}"
                        )
                    self._safe_unlink(source)
                continue
            if not source.exists():
                raise ArtifactIntegrityError(
                    f"prepared orphan is missing: {record.original_path}"
                )
            if self._hash_file(source) != record.content_hash:
                raise ArtifactIntegrityError(
                    f"orphan content changed before quarantine: {record.original_path}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._verify(destination.parent)
            self._verify(source)
            os.replace(source, destination)
            self._verify(destination)
            moved.append(record.original_path)
        return moved

    def _quarantine_orphans(self) -> list[str]:
        found: list[str] = []
        artifact_root = self._resolve("artifacts", must_exist=True)
        for path in sorted(item for item in artifact_root.rglob("*") if item.is_file()):
            path = self._verify(path)
            relative = path.relative_to(self._workspace).as_posix()
            digest = self._hash_file(path)
            orphan_id = str(uuid4())
            quarantine = f".orphans/{orphan_id}{path.suffix.lower()}"
            destination = self._resolve(quarantine)
            # Persist the intent only if the path is still unknown. A second
            # immediate transaction rechecks while holding SQLite's writer
            # reservation across the move, closing the scan/commit race.
            with session_scope(self._sessions) as session:
                self._begin_immediate(session)
                known = session.scalar(
                    select(func.count()).select_from(ArtifactRecord).where(
                        ArtifactRecord.relative_path == relative,
                        ArtifactRecord.status != ArtifactStatus.DELETED,
                    )
                )
                if known:
                    continue
                session.add(
                    ArtifactOrphanRecord(
                        id=orphan_id,
                        original_path=relative,
                        quarantine_path=quarantine,
                        size_bytes=path.stat().st_size,
                        content_hash=digest,
                    )
                )
            with session_scope(self._sessions) as session:
                self._begin_immediate(session)
                became_known = session.scalar(
                    select(func.count()).select_from(ArtifactRecord).where(
                        ArtifactRecord.relative_path == relative,
                        ArtifactRecord.status != ArtifactStatus.DELETED,
                    )
                )
                if became_known:
                    session.execute(
                        delete(ArtifactOrphanRecord).where(
                            ArtifactOrphanRecord.id == orphan_id
                        )
                    )
                    continue
                if path.exists():
                    self._verify(path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    self._verify(destination.parent)
                    os.replace(path, destination)
                    self._verify(destination)
            found.append(relative)
        return found

    def _validate_edges(
        self, session: Session, target: ArtifactRecord, edges: tuple[EdgeInput, ...]
    ) -> None:
        identities: set[tuple[str, ArtifactEdgeType]] = set()
        for edge in edges:
            identity = (edge.source_artifact_id, edge.edge_type)
            if identity in identities:
                raise ArtifactConflict("duplicate dependency edge")
            identities.add(identity)
            if edge.source_artifact_id == target.id:
                raise ArtifactConflict("artifact dependency cycle: self edge")
            source = self._require(session, edge.source_artifact_id)
            if source.project_id != target.project_id:
                raise ArtifactConflict("cross-project Artifact edges are forbidden")
            if source.status in {ArtifactStatus.INVALID, ArtifactStatus.DELETED}:
                raise ArtifactConflict("invalid or deleted Artifact cannot be a dependency")
            if edge.edge_type is ArtifactEdgeType.SUPERSEDES:
                if source.logical_id != target.logical_id:
                    raise ArtifactConflict("supersedes requires the same logical_id")
                if source.version >= target.version:
                    raise ArtifactConflict("supersedes must point to an older version")
            elif edge.edge_type in CONTENT_DEPENDENCY_EDGES and self._has_path(
                session, target.id, source.id
            ):
                raise ArtifactConflict("artifact content dependency cycle")

    @staticmethod
    def _has_path(session: Session, start: str, goal: str) -> bool:
        pending = deque([start])
        seen: set[str] = set()
        while pending:
            current = pending.popleft()
            if current == goal:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(
                session.scalars(
                    select(ArtifactEdgeRecord.target_artifact_id).where(
                        ArtifactEdgeRecord.source_artifact_id == current,
                        ArtifactEdgeRecord.edge_type.in_(CONTENT_DEPENDENCY_EDGES),
                    )
                )
            )
        return False

    def _verify_record_file(self, record: ArtifactRecord) -> None:
        if not record.relative_path or not record.content_hash:
            raise ArtifactIntegrityError("artifact file identity is incomplete")
        path = self._resolve(record.relative_path)
        try:
            self._verify(path)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact file is missing") from exc
        if not path.is_file():
            raise ArtifactIntegrityError("artifact file is missing")
        if self._hash_file(path) != record.content_hash:
            raise ArtifactIntegrityError("artifact file hash mismatch")

    def _require_snapshot_file(self, snapshot: ArtifactSnapshot) -> None:
        if not snapshot.relative_path or not snapshot.content_hash:
            raise ArtifactIntegrityError("artifact file identity is incomplete")
        path = self._resolve(snapshot.relative_path)
        try:
            self._verify(path)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact file is missing or corrupt") from exc
        if not path.is_file() or self._hash_file(path) != snapshot.content_hash:
            raise ArtifactIntegrityError("artifact file is missing or corrupt")

    def _mark_invalid(self, artifact_id: str, reason: str) -> None:
        with session_scope(self._sessions) as session:
            record = self._require(session, artifact_id)
            record.status = ArtifactStatus.INVALID
            record.invalid_reason = reason
            record.retention_since = datetime.now(UTC)

    def _finish_deleted_file_moves(self) -> None:
        """Finish physical GC for committed logical deletions, idempotently."""

        with session_scope(self._sessions) as session:
            paths = set(
                session.scalars(
                    select(ArtifactRecord.relative_path).where(
                        ArtifactRecord.status == ArtifactStatus.DELETED,
                        ArtifactRecord.relative_path.is_not(None),
                    )
                )
            )
        for relative_path in paths:
            if relative_path is None:
                continue
            with self._workspace_resource_lock(f"path:{relative_path}"):
                with session_scope(self._sessions) as session:
                    representative = session.scalar(
                        select(ArtifactRecord).where(
                            ArtifactRecord.relative_path == relative_path,
                            ArtifactRecord.status == ArtifactStatus.DELETED,
                        )
                    )
                    if representative is None:
                        continue
                    live_shares = session.scalar(
                        select(func.count()).select_from(ArtifactRecord).where(
                            ArtifactRecord.relative_path == relative_path,
                            ArtifactRecord.status != ArtifactStatus.DELETED,
                        )
                    )
                if live_shares or not representative.content_hash:
                    continue
                source = self._resolve(relative_path)
                digest = representative.content_hash.removeprefix("sha256:")
                destination = self._resolve(
                    f".trash/{digest}{representative.extension}"
                )
                if destination.exists():
                    self._verify(destination)
                    if self._hash_file(destination) != representative.content_hash:
                        raise ArtifactIntegrityError(
                            f"trash destination hash mismatch: {destination.name}"
                        )
                    self._safe_unlink(source)
                    continue
                if source.exists():
                    self._verify(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    self._verify(destination.parent)
                    try:
                        os.replace(source, destination)
                    except FileNotFoundError:
                        if not destination.exists():
                            raise
                    self._verify(destination)
                    if self._hash_file(destination) != representative.content_hash:
                        raise ArtifactIntegrityError(
                            f"trash destination hash mismatch: {destination.name}"
                        )
                    self._fsync_directory(destination.parent)

    @staticmethod
    def _json_contains_artifact(value: Any, artifact_id: str) -> bool:
        if value == artifact_id:
            return True
        if isinstance(value, dict):
            return any(
                ArtifactStore._json_contains_artifact(item, artifact_id)
                for item in value.values()
            )
        if isinstance(value, (list, tuple)):
            return any(
                ArtifactStore._json_contains_artifact(item, artifact_id)
                for item in value
            )
        return False

    @classmethod
    def _has_live_workflow_reference(cls, session: Session, artifact_id: str) -> bool:
        active_statuses = {
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_USER,
            RunStatus.WAITING_FOR_RETRY,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.RECOVERY_REQUIRED,
        }
        page_reference = session.scalar(
            select(func.count())
            .select_from(PageRecord)
            .join(WorkflowRunRecord, WorkflowRunRecord.id == PageRecord.run_id)
            .where(
                WorkflowRunRecord.status.in_(active_statuses),
                (PageRecord.current_artifact_id == artifact_id)
                | (PageRecord.page_spec_version_id == artifact_id),
            )
        )
        if page_reference:
            return True
        runs = list(
            session.scalars(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.status.in_(active_statuses)
                )
            )
        )
        active_run_ids = {run.id for run in runs}
        if any(
            run.brief_version_id == artifact_id
            or cls._json_contains_artifact(run.result_refs, artifact_id)
            for run in runs
        ):
            return True
        if active_run_ids:
            steps = session.scalars(
                select(WorkflowStepRecord).where(
                    WorkflowStepRecord.run_id.in_(active_run_ids)
                )
            )
            if any(
                cls._json_contains_artifact(step.input_refs, artifact_id)
                or cls._json_contains_artifact(step.output_refs, artifact_id)
                for step in steps
            ):
                return True
        interrupts = session.scalars(
            select(WorkflowInterruptRecord).where(
                WorkflowInterruptRecord.status == InterruptStatus.PENDING
            )
        )
        return any(
            cls._json_contains_artifact(interrupt.form_schema, artifact_id)
            or cls._json_contains_artifact(interrupt.resume_payload, artifact_id)
            for interrupt in interrupts
        )

    def _resolve(self, relative_path: str, *, must_exist: bool = False) -> Path:
        try:
            return self._paths.resolve(relative_path, must_exist=must_exist)
        except UnsafeWorkspacePath as exc:
            raise ArtifactIntegrityError(str(exc)) from exc

    def _verify(self, path: Path, *, must_exist: bool = True) -> Path:
        try:
            return self._paths.verify(path, must_exist=must_exist)
        except UnsafeWorkspacePath as exc:
            raise ArtifactIntegrityError(str(exc)) from exc

    def _safe_unlink(self, path: Path) -> None:
        try:
            verified = self._verify(path)
        except FileNotFoundError:
            return
        verified.unlink(missing_ok=True)

    def _hash_file(self, path: Path) -> str:
        path = self._verify(path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _operation_protects_artifacts(operation: ProviderOperationRecord) -> bool:
        return operation.has_unknown_attempts or operation.aggregate_status in {
            OperationAggregateStatus.PENDING,
            OperationAggregateStatus.IN_PROGRESS,
            OperationAggregateStatus.OUTCOME_UNKNOWN,
        }

    @staticmethod
    def _validate_request(request: CreateArtifact) -> None:
        if not all(
            (
                request.project_id,
                request.logical_id,
                request.kind,
                request.media_type,
                request.spec_fingerprint,
            )
        ):
            raise ValueError("Artifact identity fields must not be empty")
        if not _EXTENSION.fullmatch(request.extension):
            raise ValueError("extension must be a lowercase safe suffix such as .png")

    @staticmethod
    def _pending_edges(record: ArtifactRecord) -> tuple[EdgeInput, ...]:
        try:
            return tuple(
                EdgeInput(
                    source_artifact_id=item["source_artifact_id"],
                    edge_type=ArtifactEdgeType(item["edge_type"]),
                )
                for item in record.pending_edges
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactConflict("invalid pending dependency declaration") from error

    @staticmethod
    def _require(session: Session, artifact_id: str) -> ArtifactRecord:
        record = session.get(ArtifactRecord, artifact_id)
        if record is None:
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        return record

    @staticmethod
    def _snapshot(record: ArtifactRecord) -> ArtifactSnapshot:
        return ArtifactSnapshot(
            id=record.id,
            project_id=record.project_id,
            logical_id=record.logical_id,
            version=record.version,
            kind=record.kind,
            status=record.status,
            media_type=record.media_type,
            extension=record.extension,
            staging_path=record.staging_path,
            relative_path=record.relative_path,
            content_hash=record.content_hash,
            size_bytes=record.size_bytes,
            spec_fingerprint=record.spec_fingerprint,
            metadata=dict(record.artifact_metadata),
            operation_id=record.operation_id,
            invalid_reason=record.invalid_reason,
        )
