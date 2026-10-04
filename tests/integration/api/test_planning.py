import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from ppt_agent.api.app import create_app
from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact, EdgeInput
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.materials import (
    ParsedMaterialV1,
    SourceLocatorV1,
    TextBlockV1,
    build_text_block_id,
)
from ppt_agent.materials.service import MaterialService
from ppt_agent.materials.store import MaterialStore
from ppt_agent.materials.worker import ParserWorker
from ppt_agent.domain.state_machine import RunEvent, StepEvent
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, PlanningInputRecord, ProjectRecord
from ppt_agent.workflows.planning_store import PlanningStore


TOKEN = "t" * 43
VALID_HEADERS = {
    "host": "localhost:8000",
    "origin": "http://localhost:3000",
    "authorization": f"Bearer {TOKEN}",
}


class APIWorkflow:
    def __init__(self, store: PlanningStore) -> None:
        self.store = store

        self.before_create = None

    async def create_run(
        self, *, project_id, normalized_text, source_refs=(), validate_source_refs=False,
        source_material_refs=(),
    ):
        if self.before_create is not None:
            self.before_create()
        return self.store.create_run(
            project_id, source_refs, normalized_text,
            validate_source_refs=validate_source_refs,
            source_material_refs=source_material_refs,
        )


class RecordingSupervisor:
    def __init__(self) -> None:
        self.scheduled: list[str] = []

    def schedule(self, run_id: str) -> bool:
        if run_id in self.scheduled:
            return False
        self.scheduled.append(run_id)
        return True


def setup_api(*, max_characters: int = 100):
    directory = Path(".cache/test-planning-api")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="API planning"))
    store = PlanningStore(sessions)
    workflow = APIWorkflow(store)
    supervisor = RecordingSupervisor()
    authorized_projects: list[str] = []
    authorized_runs: list[str] = []

    async def authorize_project(value: str) -> None:
        authorized_projects.append(value)

    async def authorize_run(value: str) -> None:
        authorized_runs.append(value)

    app = create_app(
        planning_workflow=workflow,
        planning_supervisor=supervisor,
        authorize_project=authorize_project,
        authorize_run=authorize_run,
        security=LocalHTTPSecurity(token=TOKEN),
        input_limits=InputLimits(max_markdown_characters=max_characters),
    )
    return (
        engine,
        app,
        store,
        supervisor,
        project_id,
        authorized_projects,
        authorized_runs,
    )


def request(app, method: str, path: str, **kwargs):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(scenario())


def test_create_planning_run_requires_transport_auth_and_limits() -> None:
    engine, app, _, supervisor, project_id, authorized, _ = setup_api(
        max_characters=12
    )
    path = f"/api/v1/projects/{project_id}/planning-runs"
    try:
        no_auth = request(
            app,
            "POST",
            path,
            headers={"host": "localhost:8000", "origin": "http://localhost:3000"},
            json={"normalized_text": "Roadmap"},
        )
        bad_origin = request(
            app,
            "POST",
            path,
            headers={**VALID_HEADERS, "origin": "https://evil.example"},
            json={"normalized_text": "Roadmap"},
        )
        too_large = request(
            app,
            "POST",
            path,
            headers=VALID_HEADERS,
            json={"normalized_text": "x" * 13},
        )
        created = request(
            app,
            "POST",
            path,
            headers=VALID_HEADERS,
            json={"normalized_text": "Roadmap"},
        )
    finally:
        engine.dispose()

    assert no_auth.status_code == 401
    assert bad_origin.status_code == 403
    assert too_large.status_code == 413
    assert created.status_code == 202
    assert authorized == [project_id, project_id]
    assert supervisor.scheduled == [created.json()["run_id"]]


def test_resume_validates_ownership_and_schedules_each_run_once() -> None:
    engine, app, store, supervisor, project_id, _, authorized_runs = setup_api()
    try:
        run = store.create_run(project_id, (), "Roadmap")
        step = store.next_step(run.id)
        store.repository.transition_run(run.id, RunEvent.DISPATCHED)
        store.repository.transition_step(step.step_id, StepEvent.STARTED)
        interrupt = store.repository.create_interrupt(
            run_id=run.id,
            step_id=step.step_id,
            form_schema={
                "schema_name": "clarification_form",
                "schema_version": 1,
                "fields": [
                    {
                        "name": "audience",
                        "type": "text",
                        "required": True,
                    }
                ],
            },
        )
        invalid = request(
            app,
            "POST",
            f"/api/v1/runs/{run.id}/interrupts/{interrupt.id}/resume",
            headers=VALID_HEADERS,
            json={"expected_version": 1, "payload": {}},
        )
        response = request(
            app,
            "POST",
            f"/api/v1/runs/{run.id}/interrupts/{interrupt.id}/resume",
            headers=VALID_HEADERS,
            json={"expected_version": 1, "payload": {"audience": "engineers"}},
        )
        repeated = request(
            app,
            "POST",
            f"/api/v1/runs/{run.id}/interrupts/{interrupt.id}/resume",
            headers=VALID_HEADERS,
            json={"expected_version": 1, "payload": {"audience": "engineers"}},
        )
    finally:
        engine.dispose()

    assert invalid.status_code == 422
    assert response.status_code == 202
    assert repeated.status_code == 202
    assert authorized_runs == [run.id, run.id, run.id]
    assert supervisor.scheduled == [run.id]


def test_planning_projection_exposes_only_durable_refs_and_status() -> None:
    engine, app, store, _, project_id, _, _ = setup_api()
    try:
        run = store.create_run(project_id, (), "Roadmap")
        response = request(
            app,
            "GET",
            f"/api/v1/runs/{run.id}/planning",
            headers={"host": "localhost:8000", "authorization": f"Bearer {TOKEN}"},
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == {
        "run_id": run.id,
        "status": "queued",
        "error": None,
        "input_generation": 1,
        "brief_ref": None,
        "outline_ref": None,
        "design_system_ref": None,
        "page_spec_refs": [],
        "bundle_ref": None,
    }
    assert "api_key" not in response.text
    assert "response" not in response.text


def test_planning_projection_exposes_only_safe_run_failure_error() -> None:
    engine, app, store, _, project_id, _, _ = setup_api()
    try:
        run = store.create_run(project_id, (), "Roadmap")
        step = store.next_step(run.id)
        store.repository.transition_run(run.id, RunEvent.DISPATCHED)
        store.repository.transition_step(step.step_id, StepEvent.STARTED)
        store.repository.transition_step(
            step.step_id,
            StepEvent.FAILED,
            payload={
                "error": {
                    "code": "authorization=Bearer secret",
                    "provider_summary": {
                        "http_status": 400,
                        "provider_error_type": "api_key=secret",
                        "prompt": "private source text",
                    },
                }
            },
        )
        store.repository.transition_run(
            run.id,
            RunEvent.UNRECOVERABLE_FAILURE,
            payload={
                "error": {
                    "code": "authorization=Bearer secret",
                    "provider_summary": {"http_status": 400},
                }
            },
        )
        response = request(
            app,
            "GET",
            f"/api/v1/runs/{run.id}/planning",
            headers={"host": "localhost:8000", "authorization": f"Bearer {TOKEN}"},
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["error"] == {
        "code": "unknown_provider_error",
        "provider_summary": {"http_status": 400},
    }
    assert "secret" not in response.text


def setup_material_planning_api(tmp_path: Path, *, limits: InputLimits | None = None):
    workspace = tmp_path / "workspace"
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    other_project = str(uuid4())
    with session_scope(sessions) as session:
        session.add_all(
            [
                ProjectRecord(id=project_id, name="Material planning"),
                ProjectRecord(id=other_project, name="Other"),
            ]
        )
    planning_store = PlanningStore(sessions)
    workflow = APIWorkflow(planning_store)
    planning_supervisor = RecordingSupervisor()
    material_supervisor = RecordingSupervisor()
    artifacts = ArtifactStore(sessions, workspace)
    limits = limits or InputLimits(
        max_file_bytes=100_000, max_project_upload_bytes=1_000_000
    )
    material_store = MaterialStore(
        sessions, artifacts, max_project_bytes=limits.max_project_upload_bytes
    )
    material_service = MaterialService(
        material_store, artifacts, ParserWorker(workspace), limits
    )

    async def authorize_project(_value: str) -> None:
        return None

    async def authorize_run(_value: str) -> None:
        return None

    app = create_app(
        planning_workflow=workflow,
        planning_supervisor=planning_supervisor,
        material_service=material_service,
        material_supervisor=material_supervisor,
        authorize_project=authorize_project,
        authorize_run=authorize_run,
        security=LocalHTTPSecurity(token=TOKEN),
        input_limits=limits,
    )
    return (
        engine, app, planning_store, workflow, planning_supervisor,
        material_store, artifacts, project_id, other_project,
    )


def commit_artifact(
    artifacts: ArtifactStore,
    *,
    project_id: str,
    logical_id: str,
    kind: str,
    media_type: str,
    extension: str,
    payload: bytes,
    edges=(),
    metadata=None,
):
    staged = artifacts.create_staging(
        CreateArtifact(
            project_id=project_id, logical_id=logical_id, kind=kind,
            media_type=media_type, extension=extension,
            spec_fingerprint=f"fingerprint:{uuid4()}", metadata=metadata or {},
            edges=edges,
        )
    )
    artifacts.write_staging(staged.id, [payload])
    artifacts.commit_file(staged.id)
    artifacts.commit_metadata(staged.id)
    return artifacts.make_available(staged.id)


def ready_material(
    material_store: MaterialStore,
    artifacts: ArtifactStore,
    project_id: str,
    text: str,
):
    source = commit_artifact(
        artifacts, project_id=project_id, logical_id=f"source:{uuid4()}",
        kind="source_material", media_type="text/markdown", extension=".md",
        payload=text.encode(),
    )
    material = material_store.create_uploaded(
        project_id=project_id, display_name="source.md", media_type="text/markdown",
        source_artifact_id=source.id, source_bytes=len(text.encode()),
        parser_name="markdown", parser_version="1", limits={},
        limits_fingerprint="limits:v1",
    )
    claimed = material_store.claim(material.id)
    locator = SourceLocatorV1(kind="markdown", start_line=1, end_line=1)
    parsed = ParsedMaterialV1(
        material_id=material.id, media_type="text/markdown",
        source_artifact_id=source.id,
        untrusted_source_content=True,
        text_blocks=(
            TextBlockV1(
                id=build_text_block_id(material.id, locator, text),
                kind="paragraph", text=text, locator=locator,
            ),
        ),
        parser_name="markdown", parser_version="1", limits_fingerprint="limits:v1",
    )
    parsed_artifact = commit_artifact(
        artifacts, project_id=project_id,
        logical_id=f"parsed-material:{material.id}:1", kind="parsed_material",
        media_type="application/json", extension=".json",
        payload=parsed.model_dump_json().encode(),
        edges=(EdgeInput(source.id, ArtifactEdgeType.DERIVED_FROM),),
        metadata={"material_id": material.id, "schema_version": 1},
    )
    ready = material_store.complete(
        material.id, expected_version=claimed.version,
        parsed_artifact_id=parsed_artifact.id,
    )
    return ready, parsed_artifact


def test_planning_accepts_material_only_and_compiles_untrusted_boundaries(tmp_path: Path) -> None:
    (
        engine, app, planning_store, _workflow, supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path)
    material, parsed = ready_material(material_store, artifacts, project_id, "Alpha")
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [material.id]},
        )
        current = planning_store.current_input(response.json()["run_id"])
    finally:
        engine.dispose()

    assert response.status_code == 202
    assert current.source_refs == (parsed.id,)
    assert '"kind":"untrusted_source_content"' in current.normalized_text
    assert '"material_id"' in current.normalized_text
    assert "Alpha" in current.normalized_text
    assert supervisor.scheduled == [response.json()["run_id"]]


def test_planning_compiles_typed_and_material_sources_in_request_order(tmp_path: Path) -> None:
    (
        engine, app, planning_store, _workflow, _supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path)
    first, _ = ready_material(material_store, artifacts, project_id, "First")
    second, _ = ready_material(material_store, artifacts, project_id, "Second")
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS,
            json={"normalized_text": "Typed", "material_ids": [second.id, first.id]},
        )
        current = planning_store.current_input(response.json()["run_id"])
    finally:
        engine.dispose()
    assert response.status_code == 202
    assert current.normalized_text.index("Typed") < current.normalized_text.index("Second")
    assert current.normalized_text.index("Second") < current.normalized_text.index("First")


def test_planning_rejects_empty_public_refs_pending_and_foreign_materials(tmp_path: Path) -> None:
    (
        engine, app, _planning_store, _workflow, _supervisor,
        material_store, artifacts, project_id, other_project,
    ) = setup_material_planning_api(tmp_path)
    foreign, _ = ready_material(material_store, artifacts, other_project, "Foreign")
    source = commit_artifact(
        artifacts, project_id=project_id, logical_id="pending-source",
        kind="source_material", media_type="text/markdown", extension=".md",
        payload=b"pending",
    )
    pending = material_store.create_uploaded(
        project_id=project_id, display_name="pending.md", media_type="text/markdown",
        source_artifact_id=source.id, source_bytes=7, parser_name="markdown",
        parser_version="1", limits={}, limits_fingerprint="limits:v1",
    )
    try:
        empty = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={},
        )
        public_refs = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS,
            json={"normalized_text": "x", "source_refs": ["artifact"]},
        )
        pending_response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [pending.id]},
        )
        foreign_response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [foreign.id]},
        )
    finally:
        engine.dispose()
    assert empty.status_code == 422
    assert public_refs.status_code == 422
    assert pending_response.status_code == 409
    assert foreign_response.status_code == 404


def test_planning_rejects_stale_parsed_artifact(tmp_path: Path) -> None:
    (
        engine, app, _planning_store, _workflow, _supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path)
    material, parsed = ready_material(material_store, artifacts, project_id, "Stale")
    artifacts.mark_stale(parsed.id)
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [material.id]},
        )
    finally:
        engine.dispose()
    assert response.status_code == 409


def test_planning_rechecks_artifact_availability_in_creation_boundary(tmp_path: Path) -> None:
    (
        engine, app, planning_store, workflow, _supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path)
    material, parsed = ready_material(material_store, artifacts, project_id, "Race")
    workflow.before_create = lambda: artifacts.mark_stale(parsed.id)
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [material.id]},
        )
        with session_scope(planning_store._sessions) as session:
            count = session.scalar(select(func.count()).select_from(PlanningInputRecord))
    finally:
        engine.dispose()
    assert response.status_code == 409
    assert count == 0


def test_planning_rechecks_material_ready_linkage_in_creation_boundary(tmp_path: Path) -> None:
    (
        engine, app, planning_store, workflow, _supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path)
    material, _parsed = ready_material(material_store, artifacts, project_id, "Delete race")
    workflow.before_create = lambda: material_store.delete(
        material.id, expected_version=material.version
    )
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [material.id]},
        )
        with session_scope(planning_store._sessions) as session:
            count = session.scalar(select(func.count()).select_from(PlanningInputRecord))
    finally:
        engine.dispose()
    assert response.status_code == 409
    assert count == 0


def test_planning_rejects_aggregate_material_context_over_limit(tmp_path: Path) -> None:
    limits = InputLimits(
        max_file_bytes=100_000,
        max_project_upload_bytes=1_000_000,
        max_planning_source_bytes=120,
    )
    (
        engine, app, _store, _workflow, _supervisor,
        material_store, artifacts, project_id, _other,
    ) = setup_material_planning_api(tmp_path, limits=limits)
    material, _parsed = ready_material(
        material_store, artifacts, project_id, "x" * 100
    )
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/planning-runs",
            headers=VALID_HEADERS, json={"material_ids": [material.id]},
        )
    finally:
        engine.dispose()
    assert response.status_code == 413
