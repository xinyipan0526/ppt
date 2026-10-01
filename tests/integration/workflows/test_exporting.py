from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pptx import Presentation

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, PageGenerationRecord, PageRecord, ProjectRecord, QualityReportRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.exporting import ExportWorkflow


def test_export_workflow_is_cached_and_commits_pptx(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(artifacts, project_id, "page/1", "page_image", "image/png", ".png", image.getvalue(), "page-1")
    quality_artifact = commit_bytes(artifacts, project_id, "page/1/quality", "quality_report", "application/json", ".json", b'{"passed":true}', "quality-1")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page_artifact.id, page_spec_version_id=page_artifact.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page_artifact.id, page_image_artifact_id=page_artifact.id, quality_artifact_id=quality_artifact.id))
        session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality_artifact.id, passed=True, issue_summary={"issues": []}))
    workflow = ExportWorkflow(sessions, artifacts)
    try:
        first = workflow.create(project_id, run_id, "pptx")
        second = workflow.create(project_id, run_id, "pptx")
        artifact_id = workflow.export(first)
        status = workflow.status(first)
    finally:
        engine.dispose()
    assert first == second
    assert status.status == "available"
    assert status.artifact_id == artifact_id
    assert len(Presentation(BytesIO(artifacts.read_bytes(artifact_id))).slides) == 1
