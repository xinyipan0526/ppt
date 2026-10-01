"""Authenticated HTTP routes for durable source materials."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.service import MaterialService, MaterialServiceError
from ppt_agent.materials.store import (
    MaterialConflict,
    MaterialNotFound,
    MaterialQuotaExceeded,
    MaterialSnapshot,
)


ProjectAuthorizer = Callable[[str], Awaitable[None]]


class MaterialScheduler(Protocol):
    def schedule(self, material_id: str) -> bool: ...


class MaterialRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def materials_router(
    *,
    service: MaterialService,
    supervisor: MaterialScheduler,
    authorize_project: ProjectAuthorizer,
    security: LocalHTTPSecurity,
    input_limits: InputLimits,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    authenticated = [Depends(security.authenticate)]

    @router.post(
        "/projects/{project_id}/materials",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    async def upload_material(
        project_id: str,
        request: Request,
        filename: str = Query(min_length=1, max_length=1_024),
    ) -> object:
        await authorize_project(project_id)
        try:
            material = await service.upload(
                project_id=project_id,
                display_name=filename,
                declared_type=request.headers.get("content-type"),
                chunks=request.stream(),
            )
        except MaterialServiceError as exc:
            raise _service_http_error(exc) from exc
        except MaterialQuotaExceeded as exc:
            raise HTTPException(status_code=413, detail="project upload limit exceeded") from exc
        supervisor.schedule(material.id)
        return jsonable_encoder(asdict(material))

    @router.get(
        "/projects/{project_id}/materials",
        dependencies=authenticated,
    )
    async def list_materials(project_id: str) -> object:
        await authorize_project(project_id)
        materials = await asyncio.to_thread(service.store.list_project, project_id)
        return jsonable_encoder([asdict(item) for item in materials])

    @router.get(
        "/projects/{project_id}/materials/{material_id}",
        dependencies=authenticated,
    )
    async def get_material(project_id: str, material_id: str) -> object:
        await authorize_project(project_id)
        material = await _owned(service, project_id, material_id)
        return jsonable_encoder(asdict(material))

    @router.get(
        "/projects/{project_id}/materials/{material_id}/attempts",
        dependencies=authenticated,
    )
    async def list_material_attempts(project_id: str, material_id: str) -> object:
        await authorize_project(project_id)
        await _owned(service, project_id, material_id)
        attempts = await asyncio.to_thread(service.store.list_attempts, material_id)
        return jsonable_encoder([asdict(item) for item in attempts])

    @router.post(
        "/projects/{project_id}/materials/{material_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    async def retry_material(
        project_id: str, material_id: str, request: MaterialRetry
    ) -> object:
        await authorize_project(project_id)
        await _owned(service, project_id, material_id)
        try:
            material = await service.retry(
                material_id, expected_version=request.expected_version
            )
        except MaterialConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        supervisor.schedule(material.id)
        return jsonable_encoder(asdict(material))

    @router.delete(
        "/projects/{project_id}/materials/{material_id}",
        dependencies=authenticated,
    )
    async def delete_material(
        project_id: str,
        material_id: str,
        expected_version: int = Query(ge=1),
    ) -> object:
        await authorize_project(project_id)
        await _owned(service, project_id, material_id)
        try:
            material = await service.delete(material_id, expected_version=expected_version)
        except MaterialConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return jsonable_encoder(asdict(material))

    return router


async def _owned(
    service: MaterialService, project_id: str, material_id: str
) -> MaterialSnapshot:
    try:
        material = await asyncio.to_thread(service.store.get, material_id)
    except MaterialNotFound as exc:
        raise HTTPException(status_code=404, detail="material not found") from exc
    if material.project_id != project_id:
        raise HTTPException(status_code=404, detail="material not found")
    return material


def _service_http_error(exc: MaterialServiceError) -> HTTPException:
    status_code = {
        MaterialErrorCode.EMPTY_UPLOAD: 422,
        MaterialErrorCode.FILE_TOO_LARGE: 413,
        MaterialErrorCode.PROJECT_UPLOAD_LIMIT: 413,
        MaterialErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    }.get(exc.code, 422)
    return HTTPException(status_code=status_code, detail=exc.safe_detail)
