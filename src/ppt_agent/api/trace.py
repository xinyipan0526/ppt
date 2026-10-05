"""Authorized HTTP projection for a bounded workflow Run trace."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.events.trace import RunTraceService
from ppt_agent.storage.database import SessionFactory


Authorizer = Callable[[str], Awaitable[None]]


def trace_router(
    *,
    sessions: SessionFactory,
    authorize_run: Authorizer,
    security: LocalHTTPSecurity,
    input_limits: InputLimits | None = None,
) -> APIRouter:
    """Create a trace route with the standard local auth boundary."""

    limits = input_limits or InputLimits()
    service = RunTraceService(sessions)
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    @router.get("/runs/{run_id}/trace")
    async def get_trace(
        run_id: str,
        event_limit: int = Query(default=100, ge=1),
        operation_limit: int = Query(default=50, ge=1),
    ) -> object:
        if event_limit > limits.max_trace_event_limit:
            raise HTTPException(
                status_code=422,
                detail="event_limit exceeds configured trace limit",
            )
        if operation_limit > limits.max_trace_operation_limit:
            raise HTTPException(
                status_code=422,
                detail="operation_limit exceeds configured trace limit",
            )
        await authorize_run(run_id)
        try:
            payload = await asyncio.to_thread(
                service.get,
                run_id,
                event_limit=event_limit,
                operation_limit=operation_limit,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return jsonable_encoder(payload)

    return router
