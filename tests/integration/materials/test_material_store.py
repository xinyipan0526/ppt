from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact, EdgeInput
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.materials import MaterialErrorCode, MaterialStatus
from ppt_agent.materials.store import (
    MaterialConflict,
    MaterialQuotaExceeded,
    MaterialStore,
)
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import ArtifactReferenceRecord, Base


def environment(tmp_path: Path, *, quota: int = 100):
    engine = create_sqlite_engine(DatabaseConfig(tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = WorkflowRepository(sessions).create_project("Materials")
    artifacts = ArtifactStore(sessions, tmp_path / "workspace")
    return (
        MaterialStore(sessions, artifacts, max_project_bytes=quota),
        artifacts,
        sessions,
        engine,
        project_id,
    )


def available_source(artifacts: ArtifactStore, project_id: str, payload: bytes) -> str:
    staged = artifacts.create_staging(
        CreateArtifact(
            project_id=project_id,
            logical_id=f"source:{uuid4()}",
            kind="source_material",
            media_type="text/markdown",
            extension=".md",
            spec_fingerprint=f"source:{uuid4()}",
            metadata={},
        )
    )
    artifacts.write_staging(staged.id, [payload])
    artifacts.commit_file(staged.id)
    artifacts.commit_metadata(staged.id)
    return artifacts.make_available(staged.id).id


def create_material(store: MaterialStore, artifacts: ArtifactStore, project_id: str, payload: bytes = b"hello"):
    source_id = available_source(artifacts, project_id, payload)
    return store.create_uploaded(
        project_id=project_id,
        display_name="notes.md",
        media_type="text/markdown",
        source_artifact_id=source_id,
        source_bytes=len(payload),
        parser_name="markdown",
        parser_version="1",
        limits={"max_file_bytes": 50},
        limits_fingerprint="sha256:limits",
    )


def test_material_state_machine_claim_complete_and_resolve(tmp_path: Path) -> None:
    store, artifacts, _sessions, engine, project_id = environment(tmp_path)
    try:
        uploaded = create_material(store, artifacts, project_id)
        claimed = store.claim(uploaded.id)
        assert claimed is not None
        assert claimed.status is MaterialStatus.PARSING

        parsed = artifacts.create_staging(
            CreateArtifact(
                project_id=project_id,
                logical_id=f"parsed-material:{uploaded.id}:1",
                kind="parsed_material",
                media_type="application/json",
                extension=".json",
                spec_fingerprint="parsed:v1",
                metadata={"material_id": uploaded.id, "attempt": 1},
                edges=(EdgeInput(uploaded.source_artifact_id, ArtifactEdgeType.DERIVED_FROM),),
            )
        )
        artifacts.write_staging(parsed.id, [b"{}"])
        artifacts.commit_file(parsed.id)
        artifacts.commit_metadata(parsed.id)
        artifacts.make_available(parsed.id)

        ready = store.complete(
            uploaded.id,
            expected_version=claimed.version,
            parsed_artifact_id=parsed.id,
        )
        resolved = store.resolve_ready(project_id, (uploaded.id,))

        assert ready.status is MaterialStatus.READY
        assert resolved[0].material.id == uploaded.id
        assert resolved[0].parsed_artifact.id == parsed.id
        assert store.claim(uploaded.id) is None
    finally:
        engine.dispose()


def test_complete_rejects_foreign_project_artifact(tmp_path: Path) -> None:
    store, artifacts, sessions, engine, project_id = environment(tmp_path)
    try:
        other_project = WorkflowRepository(sessions).create_project("Other")
        uploaded = create_material(store, artifacts, project_id)
        claimed = store.claim(uploaded.id)
        foreign = available_source(artifacts, other_project, b"{}")

        with pytest.raises(MaterialConflict, match="same project"):
            store.complete(
                uploaded.id,
                expected_version=claimed.version,
                parsed_artifact_id=foreign,
            )
    finally:
        engine.dispose()


def test_retry_and_delete_preserve_explicit_transitions(tmp_path: Path) -> None:
    store, artifacts, _sessions, engine, project_id = environment(tmp_path)
    try:
        uploaded = create_material(store, artifacts, project_id)
        claimed = store.claim(uploaded.id)
        failed = store.fail(
            uploaded.id,
            expected_version=claimed.version,
            error_code=MaterialErrorCode.PARSER_CRASHED,
            safe_detail="worker exited",
        )
        retried = store.retry(uploaded.id, expected_version=failed.version)
        with pytest.raises(MaterialConflict, match="parsing material"):
            store.delete(uploaded.id, expected_version=retried.version)
        failed_again = store.fail(
            uploaded.id,
            expected_version=retried.version,
            error_code=MaterialErrorCode.PARSER_CRASHED,
            safe_detail="worker exited again",
        )
        deleted = store.delete(uploaded.id, expected_version=failed_again.version)

        assert retried.status is MaterialStatus.PARSING
        assert retried.attempt_count == 2
        attempts = store.list_attempts(uploaded.id)
        assert [(item.attempt_no, item.status) for item in attempts] == [
            (1, "failed"), (2, "failed")
        ]
        assert attempts[0].error_detail == "worker exited"
        assert deleted.status is MaterialStatus.DELETED
        with pytest.raises(MaterialConflict):
            store.resolve_ready(project_id, (uploaded.id,))
    finally:
        engine.dispose()


def test_reconcile_marks_interrupted_and_missing_results_failed(tmp_path: Path) -> None:
    store, artifacts, _sessions, engine, project_id = environment(tmp_path)
    try:
        first = create_material(store, artifacts, project_id, b"first")
        second = create_material(store, artifacts, project_id, b"second")
        store.claim(first.id)
        claimed = store.claim(second.id)
        store.fail(
            second.id,
            expected_version=claimed.version,
            error_code=MaterialErrorCode.PARSER_CRASHED,
            safe_detail="old failure",
        )

        report = store.reconcile()

        assert report.failed == (first.id,)
        assert store.get(first.id).error_code is MaterialErrorCode.PARSER_INTERRUPTED
    finally:
        engine.dispose()


def test_reconcile_stale_parsed_result_can_retry_to_new_result(tmp_path: Path) -> None:
    store, artifacts, sessions, engine, project_id = environment(tmp_path)
    try:
        uploaded = create_material(store, artifacts, project_id)
        claimed = store.claim(uploaded.id)

        def parsed_artifact(logical_id: str) -> str:
            staged = artifacts.create_staging(
                CreateArtifact(
                    project_id=project_id, logical_id=logical_id,
                    kind="parsed_material", media_type="application/json",
                    extension=".json", spec_fingerprint=str(uuid4()), metadata={},
                )
            )
            artifacts.write_staging(staged.id, [b"{}"])
            artifacts.commit_file(staged.id)
            artifacts.commit_metadata(staged.id)
            return artifacts.make_available(staged.id).id

        first_id = parsed_artifact(f"parsed-material:{uploaded.id}:1")
        store.complete(
            uploaded.id, expected_version=claimed.version, parsed_artifact_id=first_id
        )
        artifacts.mark_stale(first_id)
        store.reconcile()
        failed = store.get(uploaded.id)
        retried = store.retry(uploaded.id, expected_version=failed.version)
        second_id = parsed_artifact(f"parsed-material:{uploaded.id}:2")
        recovered = store.complete(
            uploaded.id, expected_version=retried.version, parsed_artifact_id=second_id
        )
        with session_scope(sessions) as session:
            references = list(
                session.scalars(
                    select(ArtifactReferenceRecord).where(
                        ArtifactReferenceRecord.owner_id == uploaded.id,
                        ArtifactReferenceRecord.role == "parsed",
                    )
                )
            )
    finally:
        engine.dispose()

    assert recovered.status is MaterialStatus.READY
    assert recovered.parsed_artifact_id == second_id
    assert len(references) == 1
    assert references[0].artifact_id == second_id


def test_project_quota_is_atomic_under_concurrent_finalize(tmp_path: Path) -> None:
    store, artifacts, _sessions, engine, project_id = environment(tmp_path, quota=15)
    first_source = available_source(artifacts, project_id, b"a" * 10)
    second_source = available_source(artifacts, project_id, b"b" * 10)

    def finalize(source_id: str):
        return store.create_uploaded(
            project_id=project_id,
            display_name="race.md",
            media_type="text/markdown",
            source_artifact_id=source_id,
            source_bytes=10,
            parser_name="markdown",
            parser_version="1",
            limits={},
            limits_fingerprint="sha256:limits",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(finalize, item) for item in (first_source, second_source)]
            outcomes = []
            for result in results:
                try:
                    outcomes.append(result.result())
                except MaterialQuotaExceeded:
                    outcomes.append("quota")

        assert sum(item == "quota" for item in outcomes) == 1
        assert len(store.list_project(project_id)) == 1
    finally:
        engine.dispose()
