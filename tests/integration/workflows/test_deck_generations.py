from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, PageGenerationRecord, PageRecord, ProductionInputRecord, ProjectRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.deck_generations import DeckGenerationConflict, DeckGenerationService


def test_deck_generation_snapshots_are_immutable_and_restore_creates_a_new_generation(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, planning_id, run_id, page_id, page_generation_id = (
        str(uuid4()) for _ in range(5)
    )
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
        session.flush()
        session.add_all([
            WorkflowRunRecord(id=planning_id, project_id=project_id, workflow_name="deck_planning", workflow_version=1, status=RunStatus.SUCCEEDED),
            WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED),
        ])
    spec = commit_bytes(artifacts, project_id, "spec", "production.spec", "application/json", ".json", b"{}", "spec")
    image = commit_bytes(artifacts, project_id, "page", "page_image", "image/png", ".png", b"png", "image")
    with session_scope(sessions) as session:
        session.add(ProductionInputRecord(run_id=run_id, project_id=project_id, planning_run_id=planning_id, planning_bundle_artifact_id=spec.id, production_spec_artifact_id=spec.id))
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", page_spec_version_id=spec.id, current_artifact_id=image.id))
        session.flush()
        session.add(PageGenerationRecord(id=page_generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=image.id))

    service = DeckGenerationService(sessions)
    try:
        first = service.create_for_run(run_id)
        restored = service.restore(first.id, expected_generation=first.generation)
        history = service.list(project_id)
        with pytest.raises(DeckGenerationConflict):
            service.restore(first.id, expected_generation=first.generation)
        with pytest.raises(LookupError):
            service.get(first.id, project_id=str(uuid4()))
    finally:
        engine.dispose()

    assert first.page_generation_ids == (page_generation_id,)
    assert restored.generation == 2
    assert restored.source_generation_id == first.id
    assert restored.page_generation_ids == first.page_generation_ids
    assert [item.id for item in history] == [first.id, restored.id]


def test_failed_deck_generation_retains_successful_page_snapshots(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "partial.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path / "partial")
    project_id, planning_id, run_id = (str(uuid4()) for _ in range(3))
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Partial"))
        session.flush()
        session.add_all([
            WorkflowRunRecord(id=planning_id, project_id=project_id, workflow_name="deck_planning", workflow_version=1, status=RunStatus.SUCCEEDED),
            WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.FAILED),
        ])
    spec = commit_bytes(artifacts, project_id, "partial-spec", "production.spec", "application/json", ".json", b"{}", "partial-spec")
    image = commit_bytes(artifacts, project_id, "partial-page", "page_image", "image/png", ".png", b"png", "partial-image")
    page_ids = [str(uuid4()), str(uuid4())]
    successful_generation = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProductionInputRecord(run_id=run_id, project_id=project_id, planning_run_id=planning_id, planning_bundle_artifact_id=spec.id, production_spec_artifact_id=spec.id))
        session.add_all([
            PageRecord(id=page_ids[0], run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", page_spec_version_id=spec.id, current_artifact_id=image.id),
            PageRecord(id=page_ids[1], run_id=run_id, page_index=2, status=PageStatus.FAILED, page_stage="rendering", page_spec_version_id=spec.id),
        ])
        session.flush()
        session.add_all([
            PageGenerationRecord(id=successful_generation, page_id=page_ids[0], generation=1, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=image.id),
            PageGenerationRecord(id=str(uuid4()), page_id=page_ids[1], generation=1, status="failed", production_spec_artifact_id=spec.id),
        ])
    try:
        snapshot = DeckGenerationService(sessions).create_for_run(run_id)
    finally:
        engine.dispose()

    assert snapshot.status == "failed"
    assert snapshot.page_generation_ids == (successful_generation,)
