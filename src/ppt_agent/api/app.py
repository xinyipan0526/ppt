"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ppt_agent import __version__
from ppt_agent.api.security import (
    AccessTokenManager,
    LocalHTTPSecurity,
    LocalRequestRejected,
)
from ppt_agent.events.sse import EventStreamService
from ppt_agent.runtime.application import ApplicationRuntime


RunAuthorizer = Callable[[str], Awaitable[None]]


class HealthResponse(BaseModel):
    """Stable response returned by the liveness endpoint."""

    status: str
    service: str
    version: str


def create_app(
    *,
    event_stream: EventStreamService | None = None,
    authorize_run: RunAuthorizer | None = None,
    security: LocalHTTPSecurity | None = None,
    runtime: ApplicationRuntime | None = None,
    token_manager: AccessTokenManager | None = None,
) -> FastAPI:
    """Create an application instance without import-time side effects."""

    if security is not None and token_manager is not None:
        raise ValueError("provide security or token_manager, not both")
    if token_manager is not None:
        security = LocalHTTPSecurity(token=token_manager.load_or_create())
    security = security or LocalHTTPSecurity()
    if runtime is not None and not security.authentication_enabled:
        raise ValueError("application runtime requires initialized local authentication")
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            await runtime.start()
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.stop()

    app = FastAPI(title="PPT Agent API", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def enforce_local_transport(request: Request, call_next: Callable) -> object:
        try:
            security.validate_transport(request)
        except LocalRequestRejected as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="ppt-agent",
            version=__version__,
        )

    @router.get("/ready")
    async def readiness() -> JSONResponse:
        ready = runtime is not None and runtime.ready
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready"},
        )

    if event_stream is not None or runtime is not None:
        if authorize_run is None:
            raise ValueError("authorize_run is required when event routes are enabled")
        if not security.authentication_enabled:
            raise ValueError("local authentication is required when event routes are enabled")

        @router.get(
            "/runs/{run_id}/snapshot",
            dependencies=[Depends(security.authenticate)],
        )
        async def run_snapshot(run_id: str) -> object:
            await authorize_run(run_id)
            service = _resolve_event_stream(event_stream, runtime)
            try:
                snapshot = await asyncio.to_thread(service.snapshot, run_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return jsonable_encoder(asdict(snapshot))

        @router.get(
            "/runs/{run_id}/events",
            dependencies=[Depends(security.authenticate)],
        )
        async def run_events(
            run_id: str,
            last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        ) -> StreamingResponse:
            await authorize_run(run_id)
            service = _resolve_event_stream(event_stream, runtime)
            try:
                await asyncio.to_thread(service.ensure_run, run_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            try:
                cursor = int(last_event_id) if last_event_id is not None else 0
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Last-Event-ID must be an integer"
                ) from exc
            if cursor < 0:
                raise HTTPException(
                    status_code=400, detail="Last-Event-ID must be non-negative"
                )
            return StreamingResponse(
                service.iter_sse(run_id, after_seq=cursor),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

    app.include_router(router)
    return app


def _resolve_event_stream(
    configured: EventStreamService | None,
    runtime: ApplicationRuntime | None,
) -> EventStreamService:
    if configured is not None:
        return configured
    if runtime is None or not runtime.ready or runtime.event_store is None:
        raise HTTPException(status_code=503, detail="application runtime is not ready")
    return EventStreamService(runtime.event_store)


app = create_app()
