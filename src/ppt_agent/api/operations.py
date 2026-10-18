"""Cost and explicit unknown-outcome recovery endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.domain.state_machine import AttemptStatus
from ppt_agent.operations.provider import ProviderOperationService, ProviderRecordNotFound
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    BudgetAccountRecord,
    CostReservationRecord,
    ProviderAttemptRecord,
    ProviderOperationRecord,
    ProviderRetryAuthorizationRecord,
    UsageRecord,
)


Authorizer = Callable[[str], Awaitable[None]]


class Scheduler(Protocol):
    def schedule(self, run_id: str) -> bool: ...


class RetryAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_attempt_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class LocalRetryAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


def operations_router(
    *, sessions: SessionFactory, operations: ProviderOperationService,
    authorize_project: Authorizer, authorize_run: Authorizer,
    security: LocalHTTPSecurity,
    repository: WorkflowRepository | None = None,
    supervisor: Scheduler | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    @router.get("/operations/{operation_id}")
    async def get_operation(operation_id: str) -> object:
        try:
            snapshot = operations.get_operation(operation_id)
            await _authorize_operation(sessions, snapshot.run_id, snapshot.id, authorize_run, authorize_project)
            attempts = operations.list_attempts(operation_id)
        except ProviderRecordNotFound as exc:
            raise HTTPException(status_code=404, detail="operation not found") from exc
        return jsonable_encoder({"operation": asdict(snapshot), "attempts": [asdict(item) for item in attempts]})

    @router.post("/operations/{operation_id}/authorize-retry")
    async def authorize_retry(operation_id: str, request: RetryAuthorization) -> object:
        try:
            snapshot = operations.get_operation(operation_id)
            await _authorize_operation(sessions, snapshot.run_id, snapshot.id, authorize_run, authorize_project)
            result = operations.authorize_retry_after_unknown(
                operation_id, unknown_attempt_id=request.expected_attempt_id, acknowledgement=request.reason,
            )
        except ProviderRecordNotFound as exc:
            raise HTTPException(status_code=404, detail="operation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"authorization_id": result}

    @router.get("/projects/{project_id}/costs")
    async def project_costs(project_id: str) -> dict:
        await authorize_project(project_id)
        with session_scope(sessions) as session:
            account_rows = session.execute(
                select(
                    BudgetAccountRecord.currency,
                    func.sum(BudgetAccountRecord.hard_limit),
                    func.sum(BudgetAccountRecord.settled),
                    func.sum(BudgetAccountRecord.active_reserved),
                    func.sum(BudgetAccountRecord.held_unknown),
                )
                .where(BudgetAccountRecord.project_id == project_id)
                .group_by(BudgetAccountRecord.currency)
                .order_by(BudgetAccountRecord.currency)
            ).all()
            rows = session.execute(
                select(UsageRecord.provider, UsageRecord.currency, func.sum(UsageRecord.actual_amount))
                .join(CostReservationRecord, CostReservationRecord.id == UsageRecord.reservation_id)
                .join(BudgetAccountRecord, BudgetAccountRecord.id == CostReservationRecord.account_id)
                .where(BudgetAccountRecord.project_id == project_id)
                .group_by(UsageRecord.provider, UsageRecord.currency)
            ).all()
        currencies = []
        for currency, hard_limit, settled, active_reserved, held_unknown in account_rows:
            hard_limit = int(hard_limit or 0)
            settled = int(settled or 0)
            active_reserved = int(active_reserved or 0)
            held_unknown = int(held_unknown or 0)
            currencies.append({
                "currency": currency,
                "hard_limit": hard_limit,
                "settled": settled,
                "active_reserved": active_reserved,
                "held_unknown": held_unknown,
                "available": max(
                    0, hard_limit - settled - active_reserved - held_unknown
                ),
            })
        return {
            "project_id": project_id,
            "currencies": currencies,
            "totals": [
                {
                    "provider": provider,
                    "currency": currency,
                    "actual_amount": amount,
                }
                for provider, currency, amount in rows
            ],
        }

    @router.post("/runs/{run_id}/authorize-local-retry")
    async def authorize_local_retry(
        run_id: str, request: LocalRetryAuthorization
    ) -> dict:
        await authorize_run(run_id)
        if repository is None or supervisor is None:
            raise HTTPException(status_code=503, detail="runtime retry is unavailable")
        with session_scope(sessions) as session:
            uncertain = session.scalar(
                select(func.count())
                .select_from(ProviderAttemptRecord)
                .join(
                    ProviderOperationRecord,
                    ProviderOperationRecord.id == ProviderAttemptRecord.operation_id,
                )
                .outerjoin(
                    ProviderRetryAuthorizationRecord,
                    ProviderRetryAuthorizationRecord.unknown_attempt_id
                    == ProviderAttemptRecord.id,
                )
                .where(
                    ProviderOperationRecord.run_id == run_id,
                    ProviderAttemptRecord.status.in_({
                        AttemptStatus.RECONCILING,
                        AttemptStatus.CANCEL_REQUESTED,
                        AttemptStatus.OUTCOME_UNKNOWN,
                    }),
                    ProviderRetryAuthorizationRecord.id.is_(None),
                )
            )
        if uncertain:
            raise HTTPException(
                status_code=409,
                detail="run has uncertain Provider attempts; authorize those operations first",
            )
        try:
            snapshot = repository.mark_recovery_retry_safe(
                run_id, evidence=f"user_acknowledged_local_retry:{request.reason}"
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        supervisor.schedule(run_id)
        return {"run_id": run_id, "status": snapshot.status.value}

    return router


async def _authorize_operation(
    sessions: SessionFactory, run_id: str | None, operation_id: str,
    authorize_run: Authorizer, authorize_project: Authorizer,
) -> None:
    if run_id:
        await authorize_run(run_id)
        return
    with session_scope(sessions) as session:
        project_id = session.scalar(
            select(CostReservationRecord.project_id)
            .join(
                ProviderAttemptRecord,
                ProviderAttemptRecord.reservation_id == CostReservationRecord.id,
            )
            .where(ProviderAttemptRecord.operation_id == operation_id)
            .limit(1)
        )
    if project_id is None:
        raise HTTPException(status_code=404, detail="operation ownership not found")
    await authorize_project(project_id)
