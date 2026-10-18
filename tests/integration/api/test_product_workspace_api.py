import asyncio
import threading
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException
from PIL import Image

from ppt_agent.api.app import create_app
from ppt_agent.api.production import production_router
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.api.workspace import workspace_router
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.runtime.export_supervisor import ExportSupervisor
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.runtime.supervisor import TaskSupervisor
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, ExportRecord, PageEditRecord, PageGenerationRecord, PageRecord, ProductionInputRecord, ProjectRecord, QualityReportRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.deck_generations import DeckGenerationService
from ppt_agent.workflows.exporting import ExportWorkflow
from ppt_agent.workflows.page_editing import PageEditingWorkflow
from ppt_agent.workflows.production_store import ProductionStore


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
        session.flush()
        session.add(PageEditRecord(
            id="pending-edit", page_id=page_id, source_generation_id=own_generation,
            result_generation_id=None, idempotency_key="pending-edit-key",
            request_payload={"schema_version": 1, "instruction": "Make it better", "resample_visual": False},
            plan_payload={
                "schema_version": 1,
                "kind": "ambiguous",
                "instruction": "Make it better",
                "target_element_ids": [],
                "target_box": None,
                "clarification_question": "你希望修改文字、样式还是视觉图片？",
            },
            status="awaiting_clarification",
        ))

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
    assert detail.json()["pending_edit"] == {
        "id": "pending-edit",
        "status": "awaiting_clarification",
        "kind": "ambiguous",
        "clarification_question": "你希望修改文字、样式还是视觉图片？",
        "target_box": None,
    }
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


def test_cancel_api_reports_the_persisted_terminal_status_idempotently(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    repository = WorkflowRepository(sessions)
    project_id = repository.create_project("Cancellation")
    run = repository.create_run(project_id, "deck_production", 1)

    async def executor(_run_id: str) -> None:
        return None

    async def complete_cancellation(run_id: str) -> None:
        repository.complete_cancellation(run_id)

    async def authorize_project(value: str) -> None:
        if value != project_id:
            raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(value: str) -> None:
        if value != run.id:
            raise HTTPException(status_code=404, detail="run not found")

    async def editor(plan, content, context):
        return content

    reads = threading.Barrier(2, timeout=5)

    class ConcurrentReads:
        def get_run(self, value: str):
            snapshot = repository.get_run(value)
            reads.wait()
            return snapshot

        def __getattr__(self, name: str):
            return getattr(repository, name)

    supervisor = TaskSupervisor(
        ConcurrentReads(), executor, cancellation_executor=complete_cancellation
    )
    export_supervisor = ExportSupervisor(ExportWorkflow(sessions, artifacts))
    app = create_app(security=LocalHTTPSecurity(token=TOKEN))
    app.include_router(production_router(
        coordinator=object(), store=ProductionStore(sessions, artifacts),
        supervisor=supervisor,
        editing=PageEditingWorkflow(sessions, artifacts, local_editor=editor, visual_editor=editor),
        exporting=ExportWorkflow(sessions, artifacts), export_supervisor=export_supervisor,
        artifacts=artifacts,
        authorize_project=authorize_project, authorize_run=authorize_run,
        security=LocalHTTPSecurity(token=TOKEN),
    ))
    try:
        async def cancel_concurrently() -> list[httpx.Response]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                return list(await asyncio.gather(
                    client.post(f"/api/v1/runs/{run.id}/cancel", headers=HEADERS),
                    client.post(f"/api/v1/runs/{run.id}/cancel", headers=HEADERS),
                ))

        responses = asyncio.run(cancel_concurrently())
    finally:
        engine.dispose()

    assert [response.status_code for response in responses] == [202, 202]
    assert [response.json()["status"] for response in responses] == ["cancelled", "cancelled"]
    assert sorted(response.json()["accepted"] for response in responses) == [False, True]


def test_cancel_api_retries_only_durable_cancellation_finalization(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    repository = WorkflowRepository(sessions)
    project_id = repository.create_project("Cancellation retry")
    run = repository.create_run(project_id, "deck_production", 1)
    normal: list[str] = []
    finalization: list[str] = []

    async def executor(run_id: str) -> None:
        normal.append(run_id)

    async def finalizer(run_id: str) -> None:
        finalization.append(run_id)
        if len(finalization) == 1:
            raise RuntimeError("cancellation finalizer unavailable")
        repository.complete_cancellation(run_id)

    async def authorize_project(value: str) -> None:
        if value != project_id:
            raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(value: str) -> None:
        if value != run.id:
            raise HTTPException(status_code=404, detail="run not found")

    async def editor(plan, content, context):
        return content

    supervisor = TaskSupervisor(repository, executor, cancellation_executor=finalizer)
    security = LocalHTTPSecurity(token=TOKEN)
    app = create_app(security=security)
    app.include_router(production_router(
        coordinator=object(), store=ProductionStore(sessions, artifacts),
        supervisor=supervisor,
        editing=PageEditingWorkflow(sessions, artifacts, local_editor=editor, visual_editor=editor),
        exporting=ExportWorkflow(sessions, artifacts), export_supervisor=ExportSupervisor(ExportWorkflow(sessions, artifacts)),
        artifacts=artifacts, authorize_project=authorize_project,
        authorize_run=authorize_run, security=security,
    ))
    try:
        first = request(app, "POST", f"/api/v1/runs/{run.id}/cancel", headers=HEADERS)
        assert first.status_code == 500
        assert repository.get_run(run.id).status is RunStatus.CANCEL_REQUESTED
        second = request(app, "POST", f"/api/v1/runs/{run.id}/cancel", headers=HEADERS)
    finally:
        engine.dispose()

    assert second.status_code == 202
    assert second.json() == {"run_id": run.id, "status": "cancelled", "accepted": False}
    assert normal == []
    assert finalization == [run.id, run.id]


def test_export_post_returns_before_blocked_encoder_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
        Base.metadata.create_all(engine)
        sessions = create_session_factory(engine)
        artifacts = ArtifactStore(sessions, tmp_path)
        project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
        with session_scope(sessions) as session:
            session.add(ProjectRecord(id=project_id, name="Async export"))
        image = BytesIO()
        Image.new("RGB", (320, 180), "navy").save(image, "PNG")
        page = commit_bytes(artifacts, project_id, "page", "page_image", "image/png", ".png", image.getvalue(), "page")
        quality = commit_bytes(artifacts, project_id, "quality", "quality_report", "application/json", ".json", b"{}", "quality")
        with session_scope(sessions) as session:
            session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
            session.flush()
            session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page.id, page_spec_version_id=page.id))
            session.flush()
            session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page.id, page_image_artifact_id=page.id, quality_artifact_id=quality.id))
            session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality.id, passed=True, issue_summary={}))

        workflow = ExportWorkflow(sessions, artifacts)
        started = threading.Event()
        release = threading.Event()
        original_render = workflow._render

        def blocked_render(output_format, pages):
            started.set()
            release.wait(timeout=5)
            return original_render(output_format, pages)

        workflow._render = blocked_render
        export_supervisor = ExportSupervisor(workflow)

        async def authorize_run(value: str) -> None:
            if value != run_id:
                raise HTTPException(status_code=404, detail="run not found")

        class Store:
            def get_run(self, value: str):
                assert value == run_id
                return type("Run", (), {"project_id": project_id})()

        app = create_app(security=LocalHTTPSecurity(token=TOKEN))
        app.include_router(production_router(
            coordinator=object(), store=Store(), supervisor=object(),
            editing=object(), exporting=workflow,
            export_supervisor=export_supervisor, artifacts=artifacts,
            authorize_project=lambda _value: None, authorize_run=authorize_run,
            security=LocalHTTPSecurity(token=TOKEN),
        ))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
                invalid_requests = [
                    {"format": "jpg", "jpeg_quality": 0},
                    {"format": "jpg", "jpeg_quality": 101},
                    {"format": "pdf", "pdf_dpi": 71},
                    {"format": "pdf", "pdf_dpi": 601},
                    {"format": "pptx", "jpeg_quality": 80},
                    {"format": "jpg", "pdf_dpi": 300},
                ]
                invalid = [
                    await client.post(
                        f"/api/v1/runs/{run_id}/exports", headers=HEADERS, json=payload,
                    )
                    for payload in invalid_requests
                ]
                with session_scope(sessions) as session:
                    assert session.query(ExportRecord).count() == 0
                response = await client.post(
                    f"/api/v1/runs/{run_id}/exports", headers=HEADERS,
                    json={"format": "pptx", "jpeg_quality": 92, "pdf_dpi": 144},
                )
            assert [item.status_code for item in invalid] == [422] * 6
            assert response.status_code == 201
            assert response.json()["status"] in {"queued", "running"}
            assert response.json()["artifact_id"] is None
            assert await asyncio.to_thread(started.wait, 2)
        finally:
            release.set()
            await export_supervisor.shutdown(timeout=2)
            engine.dispose()

    asyncio.run(scenario())
