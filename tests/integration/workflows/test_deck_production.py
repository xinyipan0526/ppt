import asyncio
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image

from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact
from ppt_agent.domain.contracts import PageSpec
from ppt_agent.domain.state_machine import RunStatus
from ppt_agent.rendering.chromium import ChromiumRenderer
from ppt_agent.rendering.layout import compile_page
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, ProjectRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.deck_production import DeckProductionWorkflow
from ppt_agent.workflows.production_store import ProductionStore
from ppt_agent.domain.production import BoxV1, ProductionSpecV1, VisualRegionV1
from ppt_agent.providers.image import ImageGenerationResult, ImageUsage


def test_deterministic_production_creates_available_page_image(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id, planning_id = str(uuid4()), str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
        session.flush()
        session.add(WorkflowRunRecord(
            id=planning_id, project_id=project_id, workflow_name="deck_planning",
            workflow_version=1, status=RunStatus.SUCCEEDED,
        ))
    artifacts = ArtifactStore(sessions, tmp_path)
    bundle = commit_bytes(artifacts, project_id, "planning/bundle", "planning.bundle", "application/json", ".json", b"{}", "bundle")
    page = compile_page(PageSpec(page_index=1, objective="Intro", page_type="content", title="Hello", body_blocks=("World",)))
    spec = ProductionSpecV1(
        project_id=project_id, planning_run_id=planning_id,
        planning_bundle_artifact_id=bundle.id, design_system_artifact_id=bundle.id,
        pages=(page,),
    )
    spec_artifact = commit_bytes(artifacts, project_id, "production/spec", "production.spec", "application/json", ".json", spec.model_dump_json().encode(), "spec")
    store = ProductionStore(sessions, artifacts)
    run_id = store.create_run(project_id, planning_id, bundle.id, spec_artifact.id)
    try:
        workflow = DeckProductionWorkflow(store, artifacts, ChromiumRenderer())
        asyncio.run(workflow.execute(run_id))
        first_artifact = store.current_pages(run_id)[0].current_artifact_id
        asyncio.run(workflow.execute(run_id))
        run = store.get_run(run_id)
        pages = store.current_pages(run_id)
    finally:
        engine.dispose()
    assert run.status is RunStatus.SUCCEEDED
    assert len(pages) == 1
    assert pages[0].current_artifact_id == first_artifact
    assert artifacts.get(pages[0].current_artifact_id).kind == "page_image"


def test_hybrid_production_persists_visual_thumbnail_and_quality(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id, planning_id = str(uuid4()), str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Hybrid"))
        session.flush()
        session.add(WorkflowRunRecord(
            id=planning_id, project_id=project_id, workflow_name="deck_planning",
            workflow_version=1, status=RunStatus.SUCCEEDED,
        ))
    artifacts = ArtifactStore(sessions, tmp_path)
    bundle = commit_bytes(artifacts, project_id, "planning/bundle", "planning.bundle", "application/json", ".json", b"{}", "bundle-hybrid")
    page = compile_page(PageSpec(page_index=1, objective="Intro", page_type="content", title="Hello", body_blocks=("World",))).model_copy(update={
        "render_mode": "hybrid",
        "visual_regions": (VisualRegionV1(id="hero", box=BoxV1(x=1200, y=100, width=600, height=800), kind="generated_image", prompt="blue geometric form"),),
    })
    spec = ProductionSpecV1(
        project_id=project_id, planning_run_id=planning_id,
        planning_bundle_artifact_id=bundle.id, design_system_artifact_id=bundle.id,
        pages=(page,),
    )
    spec_artifact = commit_bytes(artifacts, project_id, "production/spec", "production.spec", "application/json", ".json", spec.model_dump_json().encode(), "spec-hybrid")
    store = ProductionStore(sessions, artifacts)
    run_id = store.create_run(project_id, planning_id, bundle.id, spec_artifact.id)
    generated: list[str] = []

    async def generate(prompt: str, width: int, height: int) -> ImageGenerationResult:
        generated.append(prompt)
        image = Image.new("RGB", (width, height), "#2457d6")
        content = BytesIO()
        image.save(content, format="PNG")
        return ImageGenerationResult(
            response_id="det-1", media_type="image/png", width=width, height=height,
            usage=ImageUsage(generated_images=1), response_summary={"mode": "test"},
            content=content.getvalue(),
        )

    try:
        asyncio.run(DeckProductionWorkflow(store, artifacts, ChromiumRenderer(), visual_generator=generate).execute(run_id))
        page_snapshot = store.current_pages(run_id)[0]
        generation = store.generation_artifacts(page_snapshot.generation_id)
    finally:
        engine.dispose()
    assert generated == ["blue geometric form"]
    assert generation["visual"] is not None
    assert generation["thumbnail"] is not None
    assert generation["quality"] is not None
