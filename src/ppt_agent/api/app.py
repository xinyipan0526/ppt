"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ppt_agent import __version__
from ppt_agent.events.sse import EventStreamService


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
) -> FastAPI:
    """Create an application instance without import-time side effects."""

    app = FastAPI(title="PPT Agent API", version=__version__)
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="ppt-agent",
            version=__version__,
        )

    if event_stream is not None:
        if authorize_run is None:
            raise ValueError("authorize_run is required when event routes are enabled")

        @router.get("/runs/{run_id}/snapshot")
        async def run_snapshot(run_id: str) -> object:
            await authorize_run(run_id)
            try:
                snapshot = await asyncio.to_thread(event_stream.snapshot, run_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return jsonable_encoder(asdict(snapshot))

        @router.get("/runs/{run_id}/events")
        async def run_events(
            run_id: str,
            last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        ) -> StreamingResponse:
            await authorize_run(run_id)
            try:
                await asyncio.to_thread(event_stream.ensure_run, run_id)
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
                event_stream.iter_sse(run_id, after_seq=cursor),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

    app.include_router(router)
    return app


app = create_app()
