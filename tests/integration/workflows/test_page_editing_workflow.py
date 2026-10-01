import asyncio
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.domain.vision import PageEditRequestV1
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, PageGenerationRecord, PageRecord, ProjectRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.page_editing import PageEditingWorkflow


def test_content_edit_creates_immutable_generation_and_is_idempotent(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    source = BytesIO()
    Image.new("RGB", (1920, 1080), "white").save(source, "PNG")
    spec = commit_bytes(artifacts, project_id, "spec", "production.spec", "application/json", ".json", b"{}", "spec")
    image = commit_bytes(artifacts, project_id, "pages/1/image", "page_image", "image/png", ".png", source.getvalue(), "source")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", page_spec_version_id=spec.id, current_artifact_id=image.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=image.id))
    local_calls: list[str] = []
    visual_calls: list[str] = []

    async def local_editor(plan, content, context):
        local_calls.append(plan.kind)
        return content

    async def visual_editor(plan, content, context):
        visual_calls.append(plan.kind)
        return content

    workflow = PageEditingWorkflow(sessions, artifacts, local_editor=local_editor, visual_editor=visual_editor)
    try:
        first = asyncio.run(workflow.request_edit(page_id, PageEditRequestV1(instruction="把标题改成新版"), "edit-1"))
        second = asyncio.run(workflow.request_edit(page_id, PageEditRequestV1(instruction="把标题改成新版"), "edit-1"))
        history = workflow.history(page_id)
    finally:
        engine.dispose()
    assert first.id == second.id
    assert first.status == "succeeded"
    assert local_calls == ["content_edit"]
    assert visual_calls == []
    assert len(history) == 2
    assert history[0].page_image_artifact_id == image.id
    assert history[1].source_generation_id == generation_id


def test_ambiguous_edit_waits_for_clarification(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    spec = commit_bytes(artifacts, project_id, "spec", "production.spec", "application/json", ".json", b"{}", "spec")
    image_data = BytesIO(); Image.new("RGB", (1920, 1080), "white").save(image_data, "PNG")
    image = commit_bytes(artifacts, project_id, "pages/1/image", "page_image", "image/png", ".png", image_data.getvalue(), "source")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED)); session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", page_spec_version_id=spec.id, current_artifact_id=image.id)); session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=image.id))
    async def editor(plan, content, context): return content
    workflow = PageEditingWorkflow(sessions, artifacts, local_editor=editor, visual_editor=editor)
    try:
        result = asyncio.run(workflow.request_edit(page_id, PageEditRequestV1(instruction="Make it better"), "edit-2"))
    finally:
        engine.dispose()
    assert result.status == "awaiting_clarification"
    assert result.plan.clarification_question
