import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.api.workspace import workspace_router
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, PageGenerationRecord, PageRecord, ProductionInputRecord, ProjectRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.deck_generations import DeckGenerationService
from ppt_agent.workflows.page_editing import PageEditingWorkflow


TOKEN = "t" * 43
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Origin": "http://localhost:8000"}


def request(app, method: str, path: str, **kwargs):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(scenario())


def test_workspace_delivers_only_authorized_page_assets_and_redacted_projections(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, other_project, planning_id, run_id, page_id = (
        str(uuid4()) for _ in range(5)
    )
    with session_scope(sessions) as session:
        session.add_all([
            ProjectRecord(id=project_id, name="Visible"),
            ProjectRecord(id=other_project, name="Other"),
        ])
        session.flush()
        session.add_all([
            WorkflowRunRecord(id=planning_id, project_id=project_id, workflow_name="deck_planning", workflow_version=1, status=RunStatus.SUCCEEDED),
            WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED),
        ])
    spec = commit_bytes(artifacts, project_id, "spec", "production.spec", "application/json", ".json", b"{}", "spec")
    own = commit_bytes(artifacts, project_id, "own", "page_image", "image/png", ".png", b"own-image", "own")
    foreign = commit_bytes(artifacts, other_project, "foreign", "page_image", "image/png", ".png", b"foreign-image", "foreign")
    own_generation, foreign_generation = str(uuid4()), str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProductionInputRecord(run_id=run_id, project_id=project_id, planning_run_id=planning_id, planning_bundle_artifact_id=spec.id, production_spec_artifact_id=spec.id))
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", page_spec_version_id=spec.id, current_artifact_id=own.id))
        session.flush()
        session.add_all([
            PageGenerationRecord(id=own_generation, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=own.id),
            PageGenerationRecord(id=foreign_generation, page_id=page_id, generation=2, status="succeeded", production_spec_artifact_id=spec.id, page_image_artifact_id=foreign.id),
        ])

    async def authorize_project(value: str) -> None:
        if value != project_id:
            raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(value: str) -> None:
        if value != run_id:
            raise HTTPException(status_code=404, detail="run not found")

    async def editor(plan, content, context):
        return content

    editing = PageEditingWorkflow(sessions, artifacts, local_editor=editor, visual_editor=editor)
    decks = DeckGenerationService(sessions)
    deck = decks.create_for_run(run_id)
    security = LocalHTTPSecurity(token=TOKEN)
    app = create_app(security=security)
    app.include_router(workspace_router(
        sessions=sessions, artifacts=artifacts, decks=decks,
        editing=editing, authorize_project=authorize_project,
        authorize_run=authorize_run, security=security,
    ))
    try:
        detail = request(app, "GET", f"/api/v1/pages/{page_id}", headers=HEADERS)
        image = request(app, "GET", f"/api/v1/page-generations/{own_generation}/image", headers=HEADERS)
        rejected = request(app, "GET", f"/api/v1/page-generations/{foreign_generation}/image", headers=HEADERS)
        restored = request(
            app, "POST", f"/api/v1/pages/{page_id}/restore", headers=HEADERS,
            json={"source_generation_id": own_generation, "expected_current_generation_id": own_generation},
        )
        conflict = request(
            app, "POST", f"/api/v1/pages/{page_id}/restore", headers=HEADERS,
            json={"source_generation_id": own_generation, "expected_current_generation_id": own_generation},
        )
        history = request(app, "GET", f"/api/v1/pages/{page_id}/history", headers=HEADERS)
        workspace = request(app, "GET", f"/api/v1/projects/{project_id}/workspace", headers=HEADERS)
    finally:
        engine.dispose()

    assert detail.status_code == 200
    assert detail.json()["current_generation"]["id"] == own_generation
    assert "page_image_artifact_id" not in detail.text
    assert image.status_code == 200 and image.content == b"own-image"
    assert rejected.status_code == 404
    assert restored.status_code == 200
    assert restored.json()["source_generation_id"] == own_generation
    assert conflict.status_code == 409
    assert len(history.json()) == 3
    assert "page_image_artifact_id" not in history.text
    assert workspace.status_code == 200
    assert workspace.json()["project"]["name"] == "Visible"
    assert workspace.json()["deck_generations"][0]["id"] == deck.id
