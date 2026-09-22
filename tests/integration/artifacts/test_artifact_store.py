from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ppt_agent.artifacts.store import (
    ArtifactConflict,
    ArtifactIntegrityError,
    ArtifactStore,
    CreateArtifact,
    EdgeInput,
)
from ppt_agent.domain.artifacts import ArtifactEdgeType, ArtifactStatus
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, ProjectRecord
from ppt_agent.storage.workspace import UnsafeWorkspacePath, WorkspacePaths


@pytest.fixture
def tmp_path() -> Path:
    path = Path(".cache/test-artifacts") / str(uuid4())
    path.mkdir(parents=True)
    return path.resolve()


def create_store(
    tmp_path: Path,
    *,
    staging_retention: timedelta = timedelta(hours=24),
    file_validator: Callable[[Path, object], None] | None = None,
) -> tuple[ArtifactStore, object, str]:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Artifact test"))
    return (
        ArtifactStore(
            sessions,
            tmp_path / "workspace",
            staging_retention=staging_retention,
            file_validator=file_validator,
        ),
        engine,
        project_id,
    )


def request(
    project_id: str,
    logical_id: str,
    *,
    fingerprint: str | None = None,
    edges: tuple[EdgeInput, ...] = (),
) -> CreateArtifact:
    return CreateArtifact(
        project_id=project_id,
        logical_id=logical_id,
        kind="page_image",
        media_type="image/png",
        extension=".png",
        spec_fingerprint=fingerprint or f"spec:{logical_id}",
        metadata={"width": 1920, "height": 1080},
        edges=edges,
    )


def commit_available(
    store: ArtifactStore,
    create: CreateArtifact,
    payload: bytes,
) -> str:
    artifact = store.create_staging(create)
    store.write_staging(artifact.id, [payload])
    store.commit_file(artifact.id)
    store.commit_metadata(artifact.id)
    return store.make_available(artifact.id).id


def test_concurrent_staging_allocates_unique_monotonic_versions(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(
                executor.map(
                    lambda _: store.create_staging(request(project_id, "page:shared")),
                    range(8),
                )
            )
    finally:
        engine.dispose()

    assert sorted(snapshot.version for snapshot in snapshots) == list(range(1, 9))


def test_concurrent_writes_to_one_artifact_keep_the_winner_file(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    other_store = ArtifactStore(
        create_session_factory(engine),  # type: ignore[arg-type]
        tmp_path / "workspace",
    )
    artifact = store.create_staging(request(project_id, "page:shared"))

    def write(target: ArtifactStore, payload: bytes) -> str:
        try:
            target.write_staging(artifact.id, [payload])
        except ArtifactConflict:
            return "conflict"
        return "written"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda item: write(*item),
                    ((store, b"first"), (other_store, b"second")),
                )
            )
        snapshot = store.get(artifact.id)
        staging = tmp_path / "workspace" / snapshot.staging_path
    finally:
        engine.dispose()

    assert sorted(outcomes) == ["conflict", "written"]
    assert staging.is_file()
    assert f"sha256:{hashlib.sha256(staging.read_bytes()).hexdigest()}" == (
        snapshot.content_hash
    )


def test_four_phase_commit_creates_immutable_content_addressed_artifact(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        staged = store.create_staging(request(project_id, "page:1"))
        written = store.write_staging(staged.id, [b"page-", b"one"])
        file_committed = store.commit_file(staged.id)
        metadata_committed = store.commit_metadata(staged.id)
        available = store.make_available(staged.id)
    finally:
        engine.dispose()

    assert staged.status is ArtifactStatus.STAGING
    assert written.status is ArtifactStatus.STAGING
    assert written.content_hash == (
        "sha256:1898b4196a0691b7415fc2b50efb7774e48875584adf035a6523fee1359dde81"
    )
    assert file_committed.status is ArtifactStatus.FILE_COMMITTED
    assert metadata_committed.status is ArtifactStatus.METADATA_COMMITTED
    assert available.status is ArtifactStatus.AVAILABLE
    assert available.relative_path is not None
    assert available.relative_path.startswith("artifacts/18/")
    assert (tmp_path / "workspace" / available.relative_path).read_bytes() == b"page-one"


def test_expected_hash_mismatch_never_commits_file(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        staged = store.create_staging(request(project_id, "page:1"))
        with pytest.raises(ArtifactIntegrityError, match="hash"):
            store.write_staging(staged.id, [b"wrong"], expected_hash="sha256:expected")
        restored = store.get(staged.id)
    finally:
        engine.dispose()

    assert restored.status is ArtifactStatus.STAGING
    assert restored.content_hash is None


def test_file_validator_failure_leaves_artifact_uncommitted(tmp_path: Path) -> None:
    def reject_file(path: Path, _: object) -> None:
        assert path.read_bytes() == b"not-a-png"
        raise ArtifactIntegrityError("image decode failed")

    validating_store, engine, project_id = create_store(
        tmp_path, file_validator=reject_file
    )
    try:
        staged = validating_store.create_staging(request(project_id, "page:1"))
        with pytest.raises(ArtifactIntegrityError, match="decode"):
            validating_store.write_staging(staged.id, [b"not-a-png"])
        restored = validating_store.get(staged.id)
    finally:
        engine.dispose()

    assert restored.status is ArtifactStatus.STAGING
    assert restored.content_hash is None


@pytest.mark.parametrize(
    "unsafe_path",
    ("../outside.part", r"..\outside.part", r"C:\outside.part"),
)
def test_artifact_store_rejects_persisted_escaping_staging_path(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    artifact = store.create_staging(request(project_id, "page:unsafe-path"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE artifacts SET staging_path = :path WHERE id = :id"),
                {"path": unsafe_path, "id": artifact.id},
            )

        with pytest.raises(ArtifactIntegrityError, match="[Ww]orkspace|relative"):
            store.write_staging(artifact.id, [b"must-not-escape"])
    finally:
        engine.dispose()

    assert not (tmp_path / "outside.part").exists()


def test_artifact_store_rechecks_containment_after_staging_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    artifact = store.create_staging(request(project_id, "page:post-write-check"))
    original_verify = WorkspacePaths.verify

    def reject_written_staging(
        paths: WorkspacePaths,
        path: Path,
        *,
        must_exist: bool = True,
    ) -> Path:
        resolved = original_verify(paths, path, must_exist=must_exist)
        if resolved.name == f"{artifact.id}.part" and resolved.exists():
            raise UnsafeWorkspacePath("staging path escapes Workspace after write")
        return resolved

    monkeypatch.setattr(WorkspacePaths, "verify", reject_written_staging)
    try:
        with pytest.raises(ArtifactIntegrityError, match="after write"):
            store.write_staging(artifact.id, [b"payload"])
        restored = store.get(artifact.id)
    finally:
        engine.dispose()

    assert restored.content_hash is None


def test_reconcile_finishes_rename_crash_and_remaining_metadata_phases(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        staged = store.create_staging(request(project_id, "page:1"))
        written = store.write_staging(staged.id, [b"page"])
        assert written.relative_path is not None
        (tmp_path / "workspace" / written.relative_path).parent.mkdir(
            parents=True, exist_ok=True
        )
        os.replace(
            tmp_path / "workspace" / written.staging_path,
            tmp_path / "workspace" / written.relative_path,
        )

        report = store.reconcile()
        restored = store.get(staged.id)
    finally:
        engine.dispose()

    assert report.recovered == (staged.id,)
    assert restored.status is ArtifactStatus.AVAILABLE


def test_reconcile_recovers_dependencies_independent_of_scan_order(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        source = store.create_staging(request(project_id, "source"))
        derived = store.create_staging(
            request(
                project_id,
                "derived",
                edges=(EdgeInput(source.id, ArtifactEdgeType.DERIVED_FROM),),
            )
        )
        for artifact, payload in ((source, b"source"), (derived, b"derived")):
            store.write_staging(artifact.id, [payload])
            store.commit_file(artifact.id)

        with engine.connect() as connection:  # type: ignore[union-attr]
            connection.exec_driver_sql("PRAGMA reverse_unordered_selects=ON")
        report = store.reconcile()
        statuses = store_statuses(store, source.id, derived.id)
    finally:
        engine.dispose()

    assert set(report.recovered) == {source.id, derived.id}
    assert statuses == (ArtifactStatus.AVAILABLE, ArtifactStatus.AVAILABLE)


def test_reconcile_marks_available_artifact_with_missing_file_invalid(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(
            store, request(project_id, "page:1"), b"page"
        )
        snapshot = store.get(artifact_id)
        assert snapshot.relative_path is not None
        (tmp_path / "workspace" / snapshot.relative_path).unlink()

        report = store.reconcile()
        restored = store.get(artifact_id)
    finally:
        engine.dispose()

    assert report.invalid == (artifact_id,)
    assert restored.status is ArtifactStatus.INVALID


def test_available_idempotency_check_does_not_hide_missing_file(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(store, request(project_id, "page:1"), b"page")
        snapshot = store.get(artifact_id)
        assert snapshot.relative_path is not None
        (tmp_path / "workspace" / snapshot.relative_path).unlink()

        with pytest.raises(ArtifactIntegrityError, match="missing"):
            store.make_available(artifact_id)
    finally:
        engine.dispose()


def test_reconcile_quarantines_and_registers_orphan_file(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    del project_id
    orphan = tmp_path / "workspace" / "artifacts" / "aa" / "unknown.png"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    try:
        report = store.reconcile()
        records = store.list_orphans()
    finally:
        engine.dispose()

    assert report.orphans == ("artifacts/aa/unknown.png",)
    assert not orphan.exists()
    assert len(records) == 1
    assert (tmp_path / "workspace" / records[0].quarantine_path).read_bytes() == b"orphan"


def test_metadata_commit_rejects_content_dependency_cycle(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        first = store.create_staging(request(project_id, "first"))
        second = store.create_staging(
            request(
                project_id,
                "second",
                edges=(EdgeInput(first.id, ArtifactEdgeType.DERIVED_FROM),),
            )
        )
        store.write_staging(first.id, [b"first"])
        store.commit_file(first.id)
        store.write_staging(second.id, [b"second"])
        store.commit_file(second.id)
        store.commit_metadata(second.id)

        store.replace_pending_edges(
            first.id,
            (EdgeInput(second.id, ArtifactEdgeType.COMPOSED_FROM),),
        )
        with pytest.raises(ArtifactConflict, match="cycle"):
            store.commit_metadata(first.id)
    finally:
        engine.dispose()


def test_stale_propagates_only_through_content_dependencies(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        source = commit_available(store, request(project_id, "source"), b"source")
        derived = commit_available(
            store,
            request(
                project_id,
                "derived",
                edges=(EdgeInput(source, ArtifactEdgeType.DERIVED_FROM),),
            ),
            b"derived",
        )
        referenced = commit_available(
            store,
            request(
                project_id,
                "referenced",
                edges=(EdgeInput(source, ArtifactEdgeType.REFERENCES),),
            ),
            b"referenced",
        )

        changed = store.mark_stale(source)
    finally:
        engine.dispose()

    assert changed == (source, derived)
    assert store_statuses(store, source, derived, referenced) == (
        ArtifactStatus.STALE,
        ArtifactStatus.STALE,
        ArtifactStatus.AVAILABLE,
    )


def store_statuses(store: ArtifactStore, *artifact_ids: str) -> tuple[ArtifactStatus, ...]:
    return tuple(store.get(artifact_id).status for artifact_id in artifact_ids)


def test_exact_cache_hit_requires_available_artifact(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(
            store,
            request(project_id, "page:1", fingerprint="exact-config-v1"),
            b"page",
        )
        assert store.find_cache_hit(project_id, "page_image", "exact-config-v1").id == artifact_id
        store.mark_stale(artifact_id)
        miss = store.find_cache_hit(project_id, "page_image", "exact-config-v1")
    finally:
        engine.dispose()

    assert miss is None


def test_garbage_collection_preserves_explicitly_referenced_artifact(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(
            store, request(project_id, "page:1"), b"page"
        )
        store.add_reference(artifact_id, owner_type="page", owner_id="page-1", role="current")
        store.mark_stale(artifact_id)
        future = datetime.now(UTC) + timedelta(minutes=1)
        assert store.collect_garbage(before=future) == ()
        store.remove_reference(
            artifact_id, owner_type="page", owner_id="page-1", role="current"
        )
        collected = store.collect_garbage(before=future)
        restored = store.get(artifact_id)
    finally:
        engine.dispose()

    assert collected == (artifact_id,)
    assert restored.status is ArtifactStatus.DELETED


def test_current_page_reference_protects_artifact_without_mirror_row(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(store, request(project_id, "page:1"), b"page")
        store.mark_stale(artifact_id)
        with engine.begin() as connection:  # type: ignore[union-attr]
            run_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO workflow_runs "
                    "(id, project_id, workflow_name, workflow_version, status) "
                    "VALUES (:id, :project_id, 'deck_generation', 1, 'running')"
                ),
                {"id": run_id, "project_id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO pages "
                    "(id, run_id, page_index, status, current_artifact_id) "
                    "VALUES (:id, :run_id, 0, 'succeeded', :artifact_id)"
                ),
                {"id": str(uuid4()), "run_id": run_id, "artifact_id": artifact_id},
            )

        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
    finally:
        engine.dispose()

    assert collected == ()


def test_historical_run_page_does_not_permanently_protect_artifact(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    artifact_id = commit_available(store, request(project_id, "page:1"), b"page")
    store.mark_stale(artifact_id)
    with engine.begin() as connection:  # type: ignore[union-attr]
        run_id = str(uuid4())
        connection.execute(
            text(
                "INSERT INTO workflow_runs "
                "(id, project_id, workflow_name, workflow_version, status) "
                "VALUES (:id, :project_id, 'deck_generation', 1, 'succeeded')"
            ),
            {"id": run_id, "project_id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO pages "
                "(id, run_id, page_index, status, current_artifact_id) "
                "VALUES (:id, :run_id, 0, 'succeeded', :artifact_id)"
            ),
            {"id": str(uuid4()), "run_id": run_id, "artifact_id": artifact_id},
        )

    try:
        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
    finally:
        engine.dispose()

    assert collected == (artifact_id,)


def test_reference_slot_replaces_old_version_and_rejects_staging(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        first = commit_available(store, request(project_id, "page:1"), b"first")
        second = commit_available(store, request(project_id, "page:2"), b"second")
        store.add_reference(first, owner_type="page", owner_id="page-1", role="current")
        store.add_reference(second, owner_type="page", owner_id="page-1", role="current")
        store.mark_stale(first)
        store.mark_stale(second)

        staged = store.create_staging(request(project_id, "page:3"))
        with pytest.raises(ArtifactConflict, match="available"):
            store.add_reference(
                staged.id, owner_type="page", owner_id="page-3", role="current"
            )

        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
    finally:
        engine.dispose()

    assert collected == (first,)


def test_reference_slots_are_isolated_by_project(tmp_path: Path) -> None:
    store, engine, first_project_id = create_store(tmp_path)
    second_project_id = str(uuid4())
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text("INSERT INTO projects (id, name) VALUES (:id, 'Second project')"),
            {"id": second_project_id},
        )
    first = commit_available(store, request(first_project_id, "page:1"), b"first")
    second = commit_available(store, request(second_project_id, "page:1"), b"second")

    try:
        store.add_reference(first, owner_type="page", owner_id="page-1", role="current")
        store.add_reference(second, owner_type="page", owner_id="page-1", role="current")
        store.mark_stale(first)
        store.mark_stale(second)
        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
        with engine.connect() as connection:  # type: ignore[union-attr]
            reference_count = connection.scalar(
                text("SELECT COUNT(*) FROM artifact_references")
            )
    finally:
        engine.dispose()

    assert reference_count == 2
    assert collected == ()


def test_content_addressed_file_can_be_shared_by_immutable_versions(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        first = commit_available(store, request(project_id, "page:1"), b"same")
        second = commit_available(store, request(project_id, "page:2"), b"same")
        first_snapshot = store.get(first)
        second_snapshot = store.get(second)

        store.mark_stale(first)
        collected = store.collect_garbage(before=datetime.now(UTC) + timedelta(minutes=1))
        shared_path = tmp_path / "workspace" / second_snapshot.relative_path
    finally:
        engine.dispose()

    assert first_snapshot.relative_path == second_snapshot.relative_path
    assert collected == (first,)
    assert shared_path.read_bytes() == b"same"


def test_batch_collection_moves_last_shared_content_file_once(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        first = commit_available(store, request(project_id, "page:1"), b"same")
        second = commit_available(store, request(project_id, "page:2"), b"same")
        final_path = tmp_path / "workspace" / store.get(first).relative_path
        store.mark_stale(first)
        store.mark_stale(second)

        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
    finally:
        engine.dispose()

    assert set(collected) == {first, second}
    assert not final_path.exists()


def test_supersedes_chain_recovers_in_version_order(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        first = commit_available(store, request(project_id, "page:1"), b"v1")
        second = store.create_staging(
            request(
                project_id,
                "page:1",
                edges=(EdgeInput(first, ArtifactEdgeType.SUPERSEDES),),
            )
        )
        third = store.create_staging(
            request(
                project_id,
                "page:1",
                edges=(EdgeInput(second.id, ArtifactEdgeType.SUPERSEDES),),
            )
        )
        for artifact, payload in ((second, b"v2"), (third, b"v3")):
            store.write_staging(artifact.id, [payload])
            store.commit_file(artifact.id)

        with engine.connect() as connection:  # type: ignore[union-attr]
            connection.exec_driver_sql("PRAGMA reverse_unordered_selects=ON")
        store.reconcile()
        statuses = store_statuses(store, first, second.id, third.id)
    finally:
        engine.dispose()

    assert statuses == (
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.AVAILABLE,
    )


def test_reconcile_marks_missing_stale_file_invalid(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    try:
        artifact_id = commit_available(store, request(project_id, "page:1"), b"page")
        snapshot = store.get(artifact_id)
        store.mark_stale(artifact_id)
        assert snapshot.relative_path is not None
        (tmp_path / "workspace" / snapshot.relative_path).unlink()

        report = store.reconcile()
        restored = store.get(artifact_id)
    finally:
        engine.dispose()

    assert report.invalid == (artifact_id,)
    assert restored.status is ArtifactStatus.INVALID


def test_reconcile_finishes_prepared_orphan_quarantine(tmp_path: Path) -> None:
    store, engine, _ = create_store(tmp_path)
    original = tmp_path / "workspace" / "artifacts" / "aa" / "orphan.png"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"orphan")
    orphan_id = str(uuid4())
    quarantine = f".orphans/{orphan_id}.png"
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                "INSERT INTO artifact_orphans "
                "(id, original_path, quarantine_path, size_bytes, content_hash) "
                "VALUES (:id, :original, :quarantine, 6, :content_hash)"
            ),
            {
                "id": orphan_id,
                "original": "artifacts/aa/orphan.png",
                "quarantine": quarantine,
                "content_hash": "sha256:88f6811ab5d8fc6d3177f9b7609ae0fcebfda187e5046b62d38bb539e88b74d7",
            },
        )
    try:
        report = store.reconcile()
        records = store.list_orphans()
    finally:
        engine.dispose()

    assert report.orphans == ("artifacts/aa/orphan.png",)
    assert len(records) == 1
    assert not original.exists()
    assert (tmp_path / "workspace" / quarantine).read_bytes() == b"orphan"


def test_reconcile_finishes_shared_file_move_after_logical_gc_commit(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    first_id = commit_available(store, request(project_id, "page:1"), b"shared")
    second_id = commit_available(store, request(project_id, "page:2"), b"shared")
    second = store.get(second_id)
    assert second.relative_path is not None
    final = tmp_path / "workspace" / second.relative_path
    assert second.content_hash is not None
    trash = (
        tmp_path
        / "workspace"
        / ".trash"
        / f"{second.content_hash.removeprefix('sha256:')}{second.extension}"
    )
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                "UPDATE artifacts SET status = 'deleted' "
                "WHERE id IN (:first_id, :second_id)"
            ),
            {"first_id": first_id, "second_id": second_id},
        )

    try:
        store.reconcile()
        first_after = store.get(first_id)
        second_after = store.get(second_id)
    finally:
        engine.dispose()

    assert first_after.status is ArtifactStatus.DELETED
    assert second_after.status is ArtifactStatus.DELETED
    assert not final.exists()
    assert trash.read_bytes() == b"shared"


def test_concurrent_reconciliation_is_workspace_serialized(tmp_path: Path) -> None:
    store, engine, project_id = create_store(tmp_path)
    other_store = ArtifactStore(
        create_session_factory(engine),  # type: ignore[arg-type]
        tmp_path / "workspace",
    )
    artifact_id = commit_available(store, request(project_id, "page:1"), b"page")
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text("UPDATE artifacts SET status = 'deleted' WHERE id = :id"),
            {"id": artifact_id},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(
                executor.map(lambda target: target.reconcile(), (store, other_store))
            )
        snapshot = store.get(artifact_id)
    finally:
        engine.dispose()

    assert len(reports) == 2
    assert snapshot.status is ArtifactStatus.DELETED


def test_reconcile_cleans_expired_unwritten_staging_file(tmp_path: Path) -> None:
    store, engine, project_id = create_store(
        tmp_path, staging_retention=timedelta(0)
    )
    try:
        staged = store.create_staging(request(project_id, "abandoned"))
        staging_path = tmp_path / "workspace" / staged.staging_path
        staging_path.write_bytes(b"partial")

        report = store.reconcile()
        restored = store.get(staged.id)
    finally:
        engine.dispose()

    assert report.cleaned_staging == (staged.id,)
    assert restored.status is ArtifactStatus.DELETED
    assert not staging_path.exists()


def test_unresolved_unknown_attempt_protects_artifact_after_operation_succeeds(
    tmp_path: Path,
) -> None:
    store, engine, project_id = create_store(tmp_path)
    operation_id = "operation-unknown"
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                "INSERT INTO provider_operations "
                "(id, operation_kind, request_fingerprint, provider, model, "
                "aggregate_status, has_unknown_attempts) "
                "VALUES (:id, 'image.generate', 'request:v1', 'seedream', "
                "'configured-model', :status, 1)"
            ),
            {"id": operation_id, "status": OperationAggregateStatus.SUCCEEDED.value},
        )
    try:
        create = request(project_id, "visual:1")
        create = CreateArtifact(
            project_id=create.project_id,
            logical_id=create.logical_id,
            kind=create.kind,
            media_type=create.media_type,
            extension=create.extension,
            spec_fingerprint=create.spec_fingerprint,
            metadata=create.metadata,
            operation_id=operation_id,
        )
        artifact_id = commit_available(store, create, b"visual")
        store.mark_stale(artifact_id)
        collected = store.collect_garbage(
            before=datetime.now(UTC) + timedelta(minutes=1)
        )
    finally:
        engine.dispose()

    assert collected == ()
