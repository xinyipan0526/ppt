import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from ppt_agent.api.app import create_app
from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.domain.state_machine import RunEvent, StepEvent
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, ProjectRecord
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

    async def create_run(self, *, project_id, normalized_text, source_refs=()):
        return self.store.create_run(project_id, source_refs, normalized_text)


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
        "input_generation": 1,
        "brief_ref": None,
        "outline_ref": None,
        "design_system_ref": None,
        "page_spec_refs": [],
        "bundle_ref": None,
    }
    assert "api_key" not in response.text
    assert "response" not in response.text
