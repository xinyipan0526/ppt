import asyncio
from pathlib import Path
from uuid import uuid4

import httpx

from ppt_agent.api.app import create_app
from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.service import MaterialService
from ppt_agent.materials.store import MaterialStore
from ppt_agent.materials.worker import ParserWorker
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.models import Base


TOKEN = "m" * 43
HEADERS = {
    "host": "localhost:8000",
    "origin": "http://localhost:3000",
    "authorization": f"Bearer {TOKEN}",
}


class RecordingSupervisor:
    def __init__(self) -> None:
        self.scheduled: list[str] = []

    def schedule(self, material_id: str) -> bool:
        if material_id in self.scheduled:
            return False
        self.scheduled.append(material_id)
        return True


def setup_api(tmp_path: Path, *, max_file_bytes: int = 1_000):
    workspace = tmp_path / "workspace"
    engine = create_sqlite_engine(DatabaseConfig(tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    repository = WorkflowRepository(sessions)
    project_id = repository.create_project("Materials API")
    other_project = repository.create_project("Other")
    limits = InputLimits(
        max_file_bytes=max_file_bytes,
        max_project_upload_bytes=max_file_bytes * 10,
    )
    artifacts = ArtifactStore(sessions, workspace)
    store = MaterialStore(sessions, artifacts, max_project_bytes=limits.max_project_upload_bytes)
    service = MaterialService(store, artifacts, ParserWorker(workspace), limits)
    supervisor = RecordingSupervisor()
    authorized: list[str] = []

    async def authorize_project(project: str) -> None:
        authorized.append(project)

    app = create_app(
        material_service=service,
        material_supervisor=supervisor,
        authorize_project=authorize_project,
        security=LocalHTTPSecurity(token=TOKEN),
        input_limits=limits,
    )
    return engine, app, store, supervisor, project_id, other_project, authorized


def request(app, method: str, path: str, **kwargs):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(scenario())


def test_material_routes_require_auth_and_project_authorization(tmp_path: Path) -> None:
    engine, app, _store, supervisor, project_id, _other, authorized = setup_api(tmp_path)
    path = f"/api/v1/projects/{project_id}/materials?filename=notes.md"
    try:
        unauthenticated = request(
            app, "POST", path,
            headers={"host": "localhost:8000", "origin": "http://localhost:3000"},
            content=b"# Notes",
        )
        created = request(app, "POST", path, headers=HEADERS, content=b"# Notes")
        listed = request(
            app, "GET", f"/api/v1/projects/{project_id}/materials",
            headers={"host": "localhost:8000", "authorization": f"Bearer {TOKEN}"},
        )
    finally:
        engine.dispose()

    assert unauthenticated.status_code == 401
    assert created.status_code == 202
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created.json()["id"]
    assert authorized == [project_id, project_id]
    assert supervisor.scheduled == [created.json()["id"]]


def test_upload_uses_sniffed_type_over_claimed_type(tmp_path: Path) -> None:
    engine, app, _store, _supervisor, project_id, _other, _authorized = setup_api(tmp_path)
    try:
        response = request(
            app, "POST", f"/api/v1/projects/{project_id}/materials?filename=photo.png",
            headers={**HEADERS, "content-type": "image/png"},
            content=b"%PDF-1.7\nplaceholder",
        )
    finally:
        engine.dispose()
    assert response.status_code == 202
    assert response.json()["media_type"] == "application/pdf"


def test_material_routes_hide_cross_project_and_support_retry_delete(tmp_path: Path) -> None:
    engine, app, store, supervisor, project_id, other_project, _authorized = setup_api(tmp_path)
    try:
        created = request(
            app, "POST", f"/api/v1/projects/{project_id}/materials?filename=notes.md",
            headers=HEADERS, content=b"notes",
        ).json()
        hidden = request(
            app, "GET", f"/api/v1/projects/{other_project}/materials/{created['id']}",
            headers={"host": "localhost:8000", "authorization": f"Bearer {TOKEN}"},
        )
        claimed = store.claim(created["id"])
        failed = store.fail(
            created["id"], expected_version=claimed.version,
            error_code=MaterialErrorCode.PARSER_CRASHED, safe_detail="safe failure",
        )
        supervisor.scheduled.clear()
        retried = request(
            app, "POST", f"/api/v1/projects/{project_id}/materials/{created['id']}/retry",
            headers=HEADERS, json={"expected_version": failed.version},
        )
        parsing = store.get(created["id"])
        store.fail(
            created["id"], expected_version=parsing.version,
            error_code=MaterialErrorCode.PARSER_CRASHED, safe_detail="safe failure",
        )
        failed_again = store.get(created["id"])
        deleted = request(
            app, "DELETE",
            f"/api/v1/projects/{project_id}/materials/{created['id']}?expected_version={failed_again.version}",
            headers=HEADERS,
        )
    finally:
        engine.dispose()

    assert hidden.status_code == 404
    assert retried.status_code == 202
    assert supervisor.scheduled == [created["id"]]
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"


def test_material_upload_maps_limits_and_safe_errors(tmp_path: Path) -> None:
    engine, app, _store, _supervisor, project_id, _other, _authorized = setup_api(
        tmp_path, max_file_bytes=4
    )
    try:
        too_large = request(
            app, "POST", f"/api/v1/projects/{project_id}/materials?filename=x.md",
            headers=HEADERS, content=b"12345",
        )
        unsupported = request(
            app, "POST", f"/api/v1/projects/{project_id}/materials?filename=x.bin",
            headers=HEADERS, content=b"\x00\x01\x02",
        )
    finally:
        engine.dispose()
    assert too_large.status_code == 413
    assert unsupported.status_code == 415
    assert "E:\\" not in too_large.text
