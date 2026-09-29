import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ppt_agent.api.security import InputLimits
from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact
from ppt_agent.domain.artifacts import ArtifactEdgeType, ArtifactStatus
from ppt_agent.domain.materials import MaterialErrorCode, MaterialStatus
from ppt_agent.materials.service import MaterialService, MaterialServiceError
from ppt_agent.materials.store import MaterialStore
from ppt_agent.materials.worker import ParserWorker
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import ArtifactEdgeRecord, ArtifactRecord, Base


async def chunks(*values: bytes):
    for value in values:
        await asyncio.sleep(0)
        yield value


def environment(tmp_path: Path, *, limits: InputLimits | None = None):
    workspace = tmp_path / "workspace"
    engine = create_sqlite_engine(DatabaseConfig(tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = WorkflowRepository(sessions).create_project("Material service")
    artifacts = ArtifactStore(sessions, workspace)
    chosen = limits or InputLimits(
        max_file_bytes=100_000, max_project_upload_bytes=1_000_000
    )
    store = MaterialStore(sessions, artifacts, max_project_bytes=chosen.max_project_upload_bytes)
    service = MaterialService(store, artifacts, ParserWorker(workspace), chosen)
    return service, store, artifacts, sessions, engine, project_id


def test_async_artifact_writer_enforces_limit_and_removes_partial_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, _store, artifacts, _sessions, engine, project_id = environment(tmp_path)
        try:
            staged = artifacts.create_staging(
                CreateArtifact(
                    project_id=project_id, logical_id="stream:test", kind="source_material",
                    media_type="text/markdown", extension=".md", spec_fingerprint="stream",
                    metadata={},
                )
            )
            with pytest.raises(ValueError, match="byte limit"):
                await artifacts.write_staging_async(staged.id, chunks(b"123", b"456"), max_bytes=5)
            assert not (tmp_path / "workspace" / staged.staging_path).exists()
            assert artifacts.get(staged.id).content_hash is None
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_upload_commits_original_before_material_becomes_usable(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, store, artifacts, _sessions, engine, project_id = environment(tmp_path)
        try:
            material = await service.upload(
                project_id=project_id, display_name="notes.md", declared_type="text/markdown",
                chunks=chunks(b"# Title\n", b"Body"),
            )
            source = artifacts.get(material.source_artifact_id)
            assert material.status is MaterialStatus.UPLOADED
            assert source.status is ArtifactStatus.AVAILABLE
            assert artifacts.read_bytes(source.id) == b"# Title\nBody"
            assert store.get(material.id).source_artifact_id == source.id
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_upload_rejects_empty_and_oversized_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        limits = InputLimits(max_file_bytes=4, max_project_upload_bytes=100)
        service, store, _artifacts, _sessions, engine, project_id = environment(tmp_path, limits=limits)
        try:
            with pytest.raises(MaterialServiceError) as empty:
                await service.upload(
                    project_id=project_id, display_name="empty.md", declared_type="text/markdown",
                    chunks=chunks(b""),
                )
            assert empty.value.code is MaterialErrorCode.EMPTY_UPLOAD
            with pytest.raises(MaterialServiceError) as large:
                await service.upload(
                    project_id=project_id, display_name="large.md", declared_type="text/markdown",
                    chunks=chunks(b"123", b"45"),
                )
            assert large.value.code is MaterialErrorCode.FILE_TOO_LARGE
            assert store.list_project(project_id) == ()
        finally:
            engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (b"%PDF-1.7\n", "application/pdf"),
        (b"\x89PNG\r\n\x1a\nbody", "image/png"),
        (b"\xff\xd8\xffbody", "image/jpeg"),
        (b"RIFF\x04\x00\x00\x00WEBPbody", "image/webp"),
    ],
)
def test_upload_sniffs_binary_signatures_split_across_one_byte_chunks(
    tmp_path: Path, payload: bytes, expected_type: str
) -> None:
    async def scenario() -> None:
        service, _store, _artifacts, _sessions, engine, project_id = environment(tmp_path)
        try:
            material = await service.upload(
                project_id=project_id, display_name="source.bin",
                declared_type="text/markdown",
                chunks=chunks(*(bytes([value]) for value in payload)),
            )
            assert material.media_type == expected_type
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_quota_rejection_does_not_leave_available_unreferenced_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        limits = InputLimits(max_file_bytes=10, max_project_upload_bytes=10)
        service, _store, _artifacts, sessions, engine, project_id = environment(
            tmp_path, limits=limits
        )
        try:
            await service.upload(
                project_id=project_id, display_name="first.md", declared_type=None,
                chunks=chunks(b"12345678"),
            )
            with pytest.raises(MaterialServiceError) as caught:
                await service.upload(
                    project_id=project_id, display_name="second.md", declared_type=None,
                    chunks=chunks(b"abcd"),
                )
            with session_scope(sessions) as session:
                available = session.scalar(
                    select(func.count()).select_from(ArtifactRecord).where(
                        ArtifactRecord.kind == "source_material",
                        ArtifactRecord.status == ArtifactStatus.AVAILABLE,
                    )
                )
        finally:
            engine.dispose()
        assert caught.value.code is MaterialErrorCode.PROJECT_UPLOAD_LIMIT
        assert available == 1

    asyncio.run(scenario())


def test_disconnected_upload_leaves_no_ready_material(tmp_path: Path) -> None:
    async def disconnected():
        yield b"# partial"
        raise ConnectionError("browser disconnected")

    async def scenario() -> None:
        service, store, _artifacts, _sessions, engine, project_id = environment(tmp_path)
        try:
            with pytest.raises(ConnectionError):
                await service.upload(
                    project_id=project_id, display_name="partial.md",
                    declared_type="text/markdown", chunks=disconnected(),
                )
            assert store.list_project(project_id) == ()
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_parse_commits_versioned_result_and_dependency_edge(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, store, artifacts, sessions, engine, project_id = environment(tmp_path)
        try:
            material = await service.upload(
                project_id=project_id, display_name="notes.md", declared_type="text/markdown",
                chunks=chunks(b"# Parsed\n"),
            )
            ready = await service.parse(material.id)
            parsed = artifacts.get(ready.parsed_artifact_id)
            with session_scope(sessions) as session:
                edge = session.scalar(
                    select(ArtifactEdgeRecord).where(
                        ArtifactEdgeRecord.target_artifact_id == parsed.id
                    )
                )
            assert ready.status is MaterialStatus.READY
            assert parsed.status is ArtifactStatus.AVAILABLE
            assert parsed.logical_id == f"parsed-material:{material.id}:1"
            assert edge.source_artifact_id == material.source_artifact_id
            assert edge.edge_type is ArtifactEdgeType.DERIVED_FROM
            assert b'"untrusted_source_content":true' in artifacts.read_bytes(parsed.id)
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_reconcile_recovers_crash_after_parsed_artifact_commit(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        service, store, artifacts, _sessions, engine, project_id = environment(tmp_path)
        material = await service.upload(
            project_id=project_id, display_name="notes.md", declared_type="text/markdown",
            chunks=chunks(b"recover me"),
        )
        original_complete = store.complete

        def crash(*args, **kwargs):
            raise SystemExit("simulated process death")

        monkeypatch.setattr(store, "complete", crash)
        try:
            with pytest.raises(SystemExit):
                await service.parse(material.id)
            assert store.get(material.id).status is MaterialStatus.PARSING
            assert len(artifacts.list_versions(project_id, f"parsed-material:{material.id}:1")) == 1
            monkeypatch.setattr(store, "complete", original_complete)
            report = store.reconcile()
            assert report.recovered == (material.id,)
            assert store.get(material.id).status is MaterialStatus.READY
        finally:
            engine.dispose()

    asyncio.run(scenario())
