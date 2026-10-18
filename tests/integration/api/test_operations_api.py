import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException

from ppt_agent.api.app import create_app
from ppt_agent.api.operations import operations_router
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.operations.provider import OperationSnapshot, ProviderOperationService
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, BudgetAccountRecord, ProjectRecord


TOKEN = "t" * 43
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Origin": "http://localhost:8000"}


def request(app, method: str, path: str, **kwargs):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(scenario())


def test_project_costs_aggregate_budget_accounts_by_currency(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Costs"))
        session.flush()
        session.add_all(
            [
                BudgetAccountRecord(
                    id=str(uuid4()), project_id=project_id, stage="planning",
                    currency="CNY", hard_limit=1_000, soft_limit=None,
                    settled=120, active_reserved=80, held_unknown=30,
                ),
                BudgetAccountRecord(
                    id=str(uuid4()), project_id=project_id, stage="production",
                    currency="CNY", hard_limit=4_000, soft_limit=None,
                    settled=900, active_reserved=400, held_unknown=100,
                ),
                BudgetAccountRecord(
                    id=str(uuid4()), project_id=project_id, stage="visual",
                    currency="USD", hard_limit=2_000, soft_limit=None,
                    settled=250, active_reserved=50, held_unknown=25,
                ),
            ]
        )

    async def authorize_project(value: str) -> None:
        if value != project_id:
            raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(_value: str) -> None:
        raise HTTPException(status_code=404, detail="run not found")

    security = LocalHTTPSecurity(token=TOKEN)
    app = create_app(security=security)
    app.include_router(
        operations_router(
            sessions=sessions,
            operations=ProviderOperationService(sessions),
            authorize_project=authorize_project,
            authorize_run=authorize_run,
            security=security,
        )
    )
    try:
        response = request(
            app, "GET", f"/api/v1/projects/{project_id}/costs", headers=HEADERS
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["currencies"] == [
        {
            "currency": "CNY",
            "hard_limit": 5_000,
            "settled": 1_020,
            "active_reserved": 480,
            "held_unknown": 130,
            "available": 3_370,
        },
        {
            "currency": "USD",
            "hard_limit": 2_000,
            "settled": 250,
            "active_reserved": 50,
            "held_unknown": 25,
            "available": 1_675,
        },
    ]


def test_project_costs_reject_unauthorized_project(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)

    async def authorize_project(_value: str) -> None:
        raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(_value: str) -> None:
        raise HTTPException(status_code=404, detail="run not found")

    security = LocalHTTPSecurity(token=TOKEN)
    app = create_app(security=security)
    app.include_router(
        operations_router(
            sessions=sessions,
            operations=ProviderOperationService(sessions),
            authorize_project=authorize_project,
            authorize_run=authorize_run,
            security=security,
        )
    )
    try:
        response = request(
            app, "GET", "/api/v1/projects/foreign/costs", headers=HEADERS
        )
    finally:
        engine.dispose()

    assert response.status_code == 404


def test_operation_authorization_does_not_start_run_level_retry(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)

    class Operations:
        def get_operation(self, operation_id: str) -> OperationSnapshot:
            return OperationSnapshot(operation_id, "image", "fingerprint", "ark", "seedream", OperationAggregateStatus.OUTCOME_UNKNOWN, None, True, "run-1", None, None)

        def authorize_retry_after_unknown(self, operation_id: str, *, unknown_attempt_id: str, acknowledgement: str) -> str:
            return "authorization-1"

    class Repository:
        def mark_recovery_retry_safe(self, *_args, **_kwargs):
            raise AssertionError("operation authorization must not authorize the run")

    class Supervisor:
        def schedule(self, _run_id: str) -> bool:
            raise AssertionError("operation authorization must not schedule the run")

    async def authorize_project(_value: str) -> None:
        return None

    async def authorize_run(value: str) -> None:
        assert value == "run-1"

    security = LocalHTTPSecurity(token=TOKEN)
    app = create_app(security=security)
    app.include_router(operations_router(sessions=sessions, operations=Operations(), authorize_project=authorize_project, authorize_run=authorize_run, security=security, repository=Repository(), supervisor=Supervisor()))  # type: ignore[arg-type]
    try:
        response = request(app, "POST", "/api/v1/operations/operation-1/authorize-retry", headers=HEADERS, json={"expected_attempt_id": "attempt-1", "reason": "checked provider console"})
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"authorization_id": "authorization-1"}
