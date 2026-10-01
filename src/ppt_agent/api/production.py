"""Production, page editing, export, and Artifact delivery endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.vision import NormalizedBoxV1, PageEditRequestV1
from ppt_agent.workflows.exporting import ExportFormat, ExportWorkflow
from ppt_agent.workflows.page_editing import PageEditingWorkflow
from ppt_agent.workflows.production_service import ProductionCoordinator
from ppt_agent.workflows.production_store import ProductionStore


Authorizer = Callable[[str], Awaitable[None]]


class Scheduler(Protocol):
    def schedule(self, run_id: str) -> bool: ...
    async def cancel(self, run_id: str) -> bool: ...


class ProductionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planning_run_id: str = Field(min_length=1, max_length=100)


class PageEditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=255)
    resample_visual: bool = False
    target_box: NormalizedBoxV1 | None = None


class PageEditResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=2_000)
    target_box: NormalizedBoxV1 | None = None


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: ExportFormat
    forced: bool = False
    risk_acknowledgement: str | None = Field(default=None, max_length=1_000)


def production_router(
    *,
    coordinator: ProductionCoordinator,
    store: ProductionStore,
    supervisor: Scheduler,
    editing: PageEditingWorkflow,
    exporting: ExportWorkflow,
    artifacts: ArtifactStore,
    authorize_project: Authorizer,
    authorize_run: Authorizer,
    security: LocalHTTPSecurity,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    @router.post("/projects/{project_id}/production-runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_production(project_id: str, request: ProductionCreate) -> dict:
        await authorize_project(project_id)
        await authorize_run(request.planning_run_id)
        try:
            run_id = await asyncio.to_thread(coordinator.create_from_planning, project_id, request.planning_run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        supervisor.schedule(run_id)
        return {"run_id": run_id, "status": "queued"}

    @router.get("/runs/{run_id}/production")
    async def get_production(run_id: str) -> dict:
        await authorize_run(run_id)
        try:
            run = await asyncio.to_thread(store.get_run, run_id)
            pages = await asyncio.to_thread(store.current_pages, run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run_id": run.id, "status": run.status.value, "pages": jsonable_encoder([asdict(item) for item in pages])}

    @router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_run(run_id: str) -> dict:
        await authorize_run(run_id)
        try:
            accepted = await supervisor.cancel(run_id)
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "status": "cancel_requested", "accepted": accepted}

    @router.post("/pages/{page_id}/edits", status_code=status.HTTP_202_ACCEPTED)
    async def edit_page(page_id: str, request: PageEditCreate) -> object:
        try:
            run_id, _ = await asyncio.to_thread(store.get_page, page_id)
            await authorize_run(run_id)
            result = await editing.request_edit(
                page_id, PageEditRequestV1(instruction=request.instruction, resample_visual=request.resample_visual),
                request.idempotency_key, target_box=request.target_box,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return jsonable_encoder(asdict(result))

    @router.post("/pages/{page_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
    async def regenerate_page(page_id: str, request: PageEditCreate) -> object:
        run_id, _ = await asyncio.to_thread(store.get_page, page_id)
        await authorize_run(run_id)
        result = await editing.request_regeneration(page_id, request.idempotency_key)
        return jsonable_encoder(asdict(result))

    @router.post("/page-edits/{edit_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    async def resume_edit(edit_id: str, request: PageEditResume) -> object:
        try:
            current = editing.get(edit_id)
            run_id, _ = await asyncio.to_thread(store.get_page, current.page_id)
            await authorize_run(run_id)
            result = await editing.resume_edit(edit_id, instruction=request.instruction, target_box=request.target_box)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return jsonable_encoder(asdict(result))

    @router.post("/runs/{run_id}/exports", status_code=status.HTTP_201_CREATED)
    async def create_export(run_id: str, request: ExportCreate) -> dict:
        await authorize_run(run_id)
        run = await asyncio.to_thread(store.get_run, run_id)
        try:
            export_id = await asyncio.to_thread(
                exporting.create, run.project_id, run_id, request.format,
                forced=request.forced, risk_acknowledgement=request.risk_acknowledgement,
            )
            artifact_id = await asyncio.to_thread(exporting.export, export_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"export_id": export_id, "status": "available", "artifact_id": artifact_id}

    @router.get("/exports/{export_id}")
    async def get_export(export_id: str) -> object:
        try:
            snapshot = await asyncio.to_thread(exporting.status, export_id)
            await authorize_run(snapshot.run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return jsonable_encoder(asdict(snapshot))

    @router.get("/exports/{export_id}/download")
    async def download_export(
        export_id: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> Response:
        try:
            snapshot = await asyncio.to_thread(exporting.status, export_id)
            await authorize_run(snapshot.run_id)
            if snapshot.status != "available" or not snapshot.artifact_id:
                raise HTTPException(status_code=409, detail="export is not available")
            artifact = await asyncio.to_thread(artifacts.get, snapshot.artifact_id)
            content = await asyncio.to_thread(artifacts.read_bytes, snapshot.artifact_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'attachment; filename="presentation{artifact.extension}"',
            "X-Content-Type-Options": "nosniff",
        }
        if range_header is None:
            return Response(content=content, media_type=artifact.media_type, headers=headers)
        start, end = _parse_single_range(range_header, len(content))
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        return Response(
            content=content[start:end + 1], media_type=artifact.media_type,
            status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers,
        )

    return router


def _parse_single_range(value: str, length: int) -> tuple[int, int]:
    if length <= 0 or not value.startswith("bytes=") or "," in value:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{length}"},
        )
    spec = value[6:].strip()
    try:
        left, right = spec.split("-", 1)
        if not left:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError
            start, end = max(length - suffix, 0), length - 1
        else:
            start = int(left)
            end = min(int(right), length - 1) if right else length - 1
            if start < 0 or start >= length or end < start:
                raise ValueError
        return start, end
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{length}"},
        ) from None
