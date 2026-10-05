"""Integration coverage for the bounded, project-authorized Run trace."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.api.trace import trace_router
from ppt_agent.domain.costs import ReservationStatus
from ppt_agent.domain.events import EventDurability
from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.domain.state_machine import AttemptStatus, RunStatus, StepStatus
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import (
    Base,
    BudgetAccountRecord,
    CostReservationRecord,
    ProjectRecord,
    ProviderAttemptRecord,
    ProviderOperationRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)


TOKEN = "t" * 43
HEADERS = {
    "host": "localhost:8000",
    "authorization": f"Bearer {TOKEN}",
}


def request(app, path: str):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8000",
        ) as client:
            return await client.get(path, headers=HEADERS)

    return asyncio.run(scenario())


def setup_trace_api(tmp_path: Path):
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "trace.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    other_project_id = str(uuid4())
    run_id = str(uuid4())
    denied_run_id = str(uuid4())
    now = datetime.now(UTC)
    with session_scope(sessions) as session:
        session.add_all(
            [
                ProjectRecord(id=project_id, name="Trace project"),
                ProjectRecord(id=other_project_id, name="Other project"),
                WorkflowRunRecord(
                    id=run_id,
                    project_id=project_id,
                    workflow_name="deck_planning",
                    workflow_version=1,
                    status=RunStatus.FAILED,
                    error={
                        "code": "invalid_request",
                        "provider_summary": {
                            "http_status": 400,
                            "provider_error_type": "invalid_request",
                            "prompt": "private prompt",
                        },
                    },
                    next_event_seq=4,
                ),
                WorkflowRunRecord(
                    id=denied_run_id,
                    project_id=other_project_id,
                    workflow_name="deck_planning",
                    workflow_version=1,
                    status=RunStatus.QUEUED,
                    next_event_seq=1,
                ),
                WorkflowStepRecord(
                    id="step-1",
                    run_id=run_id,
                    step_key="brief",
                    generation=1,
                    status=StepStatus.FAILED,
                    attempt_count=1,
                    idempotency_key="trace-step-1",
                    input_refs=None,
                    output_refs=None,
                    error={
                        "code": "invalid_request",
                        "provider_summary": {
                            "http_status": 400,
                            "prompt": "private step prompt",
                        },
                        "response": "private step response",
                    },
                    retry_at=None,
                    recovery_metadata=None,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                WorkflowEventRecord(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    run_seq=1,
                    event_type="run.dispatched",
                    schema_version=1,
                    occurred_at=now,
                    step_id=None,
                    page_id=None,
                    operation_id=None,
                    trace_id="trace-1",
                    durability=EventDurability.CORE,
                    payload={"prompt": "private source content"},
                ),
                WorkflowEventRecord(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    run_seq=2,
                    event_type="step.failed",
                    schema_version=1,
                    occurred_at=now + timedelta(seconds=1),
                    step_id="step-1",
                    page_id=None,
                    operation_id="operation-1",
                    trace_id="trace-1",
                    durability=EventDurability.CORE,
                    payload={"response": "raw provider response"},
                ),
                WorkflowEventRecord(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    run_seq=3,
                    event_type="run.failed",
                    schema_version=1,
                    occurred_at=now + timedelta(seconds=2),
                    step_id=None,
                    page_id=None,
                    operation_id="operation-2",
                    trace_id="trace-1",
                    durability=EventDurability.CORE,
                    payload={"authorization": "Bearer secret"},
                ),
                BudgetAccountRecord(
                    id="account-1",
                    project_id=project_id,
                    stage="planning",
                    currency="USD",
                    hard_limit=1000,
                    soft_limit=None,
                    settled=0,
                    active_reserved=1,
                    held_unknown=0,
                ),
                CostReservationRecord(
                    id="reservation-1",
                    account_id="account-1",
                    project_id=project_id,
                    stage="planning",
                    operation_id="operation-1",
                    reservation_key="trace-reservation-1",
                    amount_reserved=1,
                    amount_settled=0,
                    budget_deviation=0,
                    currency="USD",
                    pricing_version="test-v1",
                    status=ReservationStatus.ACTIVE,
                    expires_at=None,
                ),
                ProviderOperationRecord(
                    id="operation-1",
                    operation_kind="text_generation",
                    request_fingerprint="private-request-fingerprint",
                    provider="ark",
                    model="model-1",
                    aggregate_status=OperationAggregateStatus.FAILED,
                    selected_attempt_id=None,
                    has_unknown_attempts=False,
                    run_id=run_id,
                    step_id="step-1",
                    page_id=None,
                ),
                ProviderOperationRecord(
                    id="operation-2",
                    operation_kind="image_generation",
                    request_fingerprint="other-private-fingerprint",
                    provider="ark",
                    model="model-2",
                    aggregate_status=OperationAggregateStatus.FAILED,
                    selected_attempt_id=None,
                    has_unknown_attempts=False,
                    run_id=run_id,
                    step_id=None,
                    page_id=None,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProviderAttemptRecord(
                    id="attempt-1",
                    operation_id="operation-1",
                    attempt_no=1,
                    status=AttemptStatus.FAILED,
                    idempotency_key="private-idempotency-key",
                    provider_job_id="private-job-id",
                    reservation_id="reservation-1",
                    request_started_at=now,
                    response_received_at=now + timedelta(seconds=1),
                    usage_record_id=None,
                    error_code="invalid_request",
                    response_summary={
                        "http_status": 400,
                        "provider_error_type": "invalid_request",
                        "raw_response": "private provider body",
                    },
                ),
            ]
        )

    async def authorize_run(candidate: str) -> None:
        if candidate != run_id:
            raise HTTPException(status_code=404, detail="run not found")

    app = create_app(security=LocalHTTPSecurity(token=TOKEN))
    app.include_router(
        trace_router(
            sessions=sessions,
            authorize_run=authorize_run,
            security=LocalHTTPSecurity(token=TOKEN),
        )
    )
    return engine, app, project_id, run_id, denied_run_id


def test_run_trace_returns_authorized_safe_ordered_projection(tmp_path: Path) -> None:
    """Removing the trace route or its safe allowlists must break this contract."""

    engine, app, project_id, run_id, _ = setup_trace_api(tmp_path)
    try:
        response = request(app, f"/api/v1/runs/{run_id}/trace")
    finally:
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"] == {
        "id": run_id,
        "project_id": project_id,
        "workflow_name": "deck_planning",
        "workflow_version": 1,
        "status": "failed",
        "error": {
            "code": "invalid_request",
            "provider_summary": {
                "http_status": 400,
                "provider_error_type": "invalid_request",
            },
        },
    }
    assert [item["run_seq"] for item in payload["events"]] == [1, 2, 3]
    assert [item["event_type"] for item in payload["events"]] == [
        "run.dispatched", "step.failed", "run.failed",
    ]
    assert payload["steps"] == [{
        "id": "step-1",
        "status": "failed",
        "step_key": "brief",
        "generation": 1,
        "attempt_count": 1,
        "error": {
            "code": "invalid_request",
            "provider_summary": {"http_status": 400},
        },
    }]
    assert payload["operations"][0]["operation"] == {
        "id": "operation-1",
        "operation_kind": "text_generation",
        "provider": "ark",
        "model": "model-1",
        "aggregate_status": "failed",
        "selected_attempt_id": None,
        "has_unknown_attempts": False,
        "step_id": "step-1",
        "page_id": None,
    }
    assert payload["operations"][0]["attempts"] == [{
        "id": "attempt-1",
        "attempt_no": 1,
        "status": "failed",
        "request_started_at": payload["operations"][0]["attempts"][0]["request_started_at"],
        "response_received_at": payload["operations"][0]["attempts"][0]["response_received_at"],
        "error_code": "invalid_request",
        "response_summary": {
            "http_status": 400,
            "provider_error_type": "invalid_request",
        },
    }]
    for forbidden in (
        "private prompt", "private source content", "raw provider response",
        "Bearer secret", "private-request-fingerprint", "private-idempotency-key",
        "private-job-id", "private provider body", "private step prompt",
        "private step response",
    ):
        assert forbidden not in response.text


def test_run_trace_denies_a_run_outside_the_authorized_project(tmp_path: Path) -> None:
    """Removing the authorize_run call would disclose another project's Run."""

    engine, app, _, _, denied_run_id = setup_trace_api(tmp_path)
    try:
        response = request(app, f"/api/v1/runs/{denied_run_id}/trace")
    finally:
        engine.dispose()

    assert response.status_code == 404


def test_run_trace_honors_event_and_operation_limits(tmp_path: Path) -> None:
    """Dropping either SQL limit would make this response grow without bound."""

    engine, app, _, run_id, _ = setup_trace_api(tmp_path)
    try:
        response = request(
            app,
            f"/api/v1/runs/{run_id}/trace?event_limit=1&operation_limit=1",
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert [item["run_seq"] for item in response.json()["events"]] == [1]
    assert [item["operation"]["id"] for item in response.json()["operations"]] == [
        "operation-1"
    ]
