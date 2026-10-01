"""Narrow, ownership-checked read models for the browser product workspace."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    DeckGenerationRecord,
    PageEditRecord,
    PageGenerationRecord,
    PageRecord,
    ProjectRecord,
    SourceMaterialRecord,
    WorkflowRunRecord,
)
from ppt_agent.workflows.deck_generations import (
    DeckGenerationConflict,
    DeckGenerationService,
)
from ppt_agent.workflows.page_editing import PageEditingWorkflow


Authorizer = Callable[[str], Awaitable[None]]


class PageRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_generation_id: str = Field(min_length=1, max_length=100)
    expected_current_generation_id: str = Field(min_length=1, max_length=100)


class DeckRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_generation: int = Field(ge=1)


def workspace_router(
    *,
    sessions: SessionFactory,
    artifacts: ArtifactStore,
    decks: DeckGenerationService,
    editing: PageEditingWorkflow,
    authorize_project: Authorizer,
    authorize_run: Authorizer,
    security: LocalHTTPSecurity,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    @router.get("/workspace")
    async def workspace_summary() -> dict:
        def load() -> dict:
            with session_scope(sessions) as session:
                projects = list(
                    session.scalars(select(ProjectRecord).order_by(ProjectRecord.created_at.desc()))
                )
                return {
                    "projects": [
                        _project_summary(session, item) for item in projects
                    ]
                }
        return jsonable_encoder(await asyncio.to_thread(load))

    @router.get("/projects/{project_id}/workspace")
    async def project_workspace(project_id: str) -> dict:
        await authorize_project(project_id)

        def load() -> dict:
            with session_scope(sessions) as session:
                project = session.get(ProjectRecord, project_id)
                if project is None:
                    raise LookupError("project not found")
                runs = list(
                    session.scalars(
                        select(WorkflowRunRecord)
                        .where(WorkflowRunRecord.project_id == project_id)
                        .order_by(WorkflowRunRecord.created_at.desc())
                    )
                )
                materials = list(
                    session.scalars(
                        select(SourceMaterialRecord)
                        .where(SourceMaterialRecord.project_id == project_id)
                        .order_by(SourceMaterialRecord.created_at.desc())
                    )
                )
                return {
                    "project": {"id": project.id, "name": project.name},
                    "runs": [
                        {
                            "id": item.id,
                            "workflow": item.workflow_name,
                            "status": item.status.value,
                            "created_at": item.created_at,
                        }
                        for item in runs
                    ],
                    "materials": [
                        {
                            "id": item.id,
                            "filename": item.filename,
                            "media_type": item.media_type,
                            "status": item.status.value,
                            "error": item.error,
                            "created_at": item.created_at,
                        }
                        for item in materials
                    ],
                }

        payload = await asyncio.to_thread(load)
        payload["deck_generations"] = jsonable_encoder(
            [asdict(item) for item in await asyncio.to_thread(decks.list, project_id)]
        )
        return jsonable_encoder(payload)

    @router.get("/projects/{project_id}/deck-generations")
    async def deck_history(project_id: str) -> object:
        await authorize_project(project_id)
        return jsonable_encoder(
            [asdict(item) for item in await asyncio.to_thread(decks.list, project_id)]
        )

    @router.post("/projects/{project_id}/deck-generations/{generation_id}/restore")
    async def restore_deck(
        project_id: str, generation_id: str, request: DeckRestoreRequest
    ) -> object:
        await authorize_project(project_id)
        try:
            await asyncio.to_thread(decks.get, generation_id, project_id=project_id)
            result = await asyncio.to_thread(
                decks.restore,
                generation_id,
                expected_generation=request.expected_generation,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeckGenerationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return jsonable_encoder(asdict(result))

    @router.get("/pages/{page_id}")
    async def page_detail(page_id: str) -> dict:
        run_id = await asyncio.to_thread(_page_run_id, sessions, page_id)
        await authorize_run(run_id)
        try:
            return await asyncio.to_thread(
                _page_projection, sessions, artifacts, page_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/pages/{page_id}/history")
    async def page_history(page_id: str) -> list[dict]:
        run_id = await asyncio.to_thread(_page_run_id, sessions, page_id)
        await authorize_run(run_id)
        history = await asyncio.to_thread(_page_history, sessions, artifacts, page_id)
        for item in history:
            item.pop("page_image_artifact_id", None)
        return history

    @router.post("/pages/{page_id}/restore")
    async def restore_page(page_id: str, request: PageRestoreRequest) -> object:
        run_id = await asyncio.to_thread(_page_run_id, sessions, page_id)
        await authorize_run(run_id)
        try:
            result = await asyncio.to_thread(
                editing.restore_generation,
                page_id,
                request.source_generation_id,
                expected_current_generation_id=request.expected_current_generation_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return jsonable_encoder(asdict(result))

    @router.get("/page-generations/{generation_id}/{asset}")
    async def page_asset(generation_id: str, asset: str) -> Response:
        if asset not in {"image", "thumbnail"}:
            raise HTTPException(status_code=404, detail="page asset not found")
        try:
            run_id, project_id, artifact_id = await asyncio.to_thread(
                _resolve_page_asset, sessions, generation_id, asset
            )
            await authorize_run(run_id)
            artifact = await asyncio.to_thread(artifacts.get, artifact_id)
            if artifact.project_id != project_id or not artifact.media_type.startswith("image/"):
                raise LookupError("page asset not found")
            content = await asyncio.to_thread(artifacts.read_bytes, artifact_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type=artifact.media_type,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


def _project_summary(session, project: ProjectRecord) -> dict:
    latest = session.scalar(
        select(WorkflowRunRecord)
        .where(WorkflowRunRecord.project_id == project.id)
        .order_by(WorkflowRunRecord.created_at.desc())
    )
    return {
        "id": project.id,
        "name": project.name,
        "created_at": project.created_at,
        "latest_run": None if latest is None else {
            "id": latest.id,
            "workflow": latest.workflow_name,
            "status": latest.status.value,
        },
        "material_count": int(
            session.scalar(
                select(func.count(SourceMaterialRecord.id)).where(
                    SourceMaterialRecord.project_id == project.id
                )
            ) or 0
        ),
        "deck_generation_count": int(
            session.scalar(
                select(func.count(DeckGenerationRecord.id)).where(
                    DeckGenerationRecord.project_id == project.id
                )
            ) or 0
        ),
    }


def _page_run_id(sessions: SessionFactory, page_id: str) -> str:
    with session_scope(sessions) as session:
        page = session.get(PageRecord, page_id)
        if page is None:
            raise LookupError("page not found")
        return page.run_id


def _page_projection(
    sessions: SessionFactory, artifacts: ArtifactStore, page_id: str
) -> dict:
    history = _page_history(sessions, artifacts, page_id)
    with session_scope(sessions) as session:
        page = session.get(PageRecord, page_id)
        if page is None:
            raise LookupError("page not found")
        current = next(
            (
                item for item in reversed(history)
                if item["page_image_url"] is not None
                and item["page_image_artifact_id"] == page.current_artifact_id
            ),
            history[-1] if history else None,
        )
        if current is None:
            raise LookupError("page generation not found")
        current.pop("page_image_artifact_id", None)
        return {
            "id": page.id,
            "run_id": page.run_id,
            "page_index": page.page_index,
            "status": page.status.value,
            "stage": page.page_stage.value if page.page_stage else None,
            "error": page.error,
            "current_generation": current,
        }


def _page_history(
    sessions: SessionFactory, artifacts: ArtifactStore, page_id: str
) -> list[dict]:
    with session_scope(sessions) as session:
        page = session.get(PageRecord, page_id)
        if page is None:
            raise LookupError("page not found")
        run = session.get(WorkflowRunRecord, page.run_id)
        if run is None:
            raise LookupError("production run not found")
        run_project_id = run.project_id
        generations = list(
            session.scalars(
                select(PageGenerationRecord)
                .where(PageGenerationRecord.page_id == page_id)
                .order_by(PageGenerationRecord.generation)
            )
        )
        edit_prompts = {
            item.result_generation_id: item.request_payload.get("instruction")
            for item in session.scalars(
                select(PageEditRecord).where(PageEditRecord.page_id == page_id)
            )
            if item.result_generation_id
        }
    result = []
    for item in generations:
        spec = None
        quality = None
        if item.render_manifest_artifact_id:
            manifest = _read_project_json(
                artifacts, item.render_manifest_artifact_id, run_project_id
            )
            spec = manifest.get("page") if isinstance(manifest, dict) else None
        if item.quality_artifact_id:
            quality = _read_project_json(
                artifacts, item.quality_artifact_id, run_project_id
            )
        if isinstance(spec, dict):
            spec = _without_internal_artifact_refs(spec)
        text_entries = [] if not spec else [
            {"id": block["id"], "role": block["role"], "text": block["text"]}
            for block in spec.get("text_blocks", [])
        ]
        design_prompts = [] if not spec else [
            region["prompt"] for region in spec.get("visual_regions", [])
            if region.get("prompt")
        ]
        result.append({
            "id": item.id,
            "generation": item.generation,
            "status": item.status,
            "source_generation_id": item.source_generation_id,
            "delivery_text": text_entries,
            "design_prompt": "\n\n".join(design_prompts),
            "local_edit_prompt": edit_prompts.get(item.id),
            "spec": spec,
            "quality": quality,
            "page_image_url": f"/api/v1/page-generations/{item.id}/image" if item.page_image_artifact_id else None,
            "thumbnail_url": f"/api/v1/page-generations/{item.id}/thumbnail" if item.thumbnail_artifact_id else None,
            "page_image_artifact_id": item.page_image_artifact_id,
            "created_at": item.created_at,
        })
    return jsonable_encoder(result)


def _read_project_json(
    artifacts: ArtifactStore, artifact_id: str, project_id: str
) -> dict | None:
    snapshot = artifacts.get(artifact_id)
    if snapshot.project_id != project_id or snapshot.media_type != "application/json":
        return None
    value = json.loads(artifacts.read_bytes(artifact_id))
    return value if isinstance(value, dict) else None


def _without_internal_artifact_refs(value):
    if isinstance(value, dict):
        return {
            key: _without_internal_artifact_refs(item)
            for key, item in value.items()
            if not key.endswith(("_artifact_id", "_artifact_ids", "_ref", "_refs"))
        }
    if isinstance(value, list):
        return [_without_internal_artifact_refs(item) for item in value]
    return value


def _resolve_page_asset(
    sessions: SessionFactory, generation_id: str, asset: str
) -> tuple[str, str, str]:
    with session_scope(sessions) as session:
        generation = session.get(PageGenerationRecord, generation_id)
        if generation is None:
            raise LookupError("page generation not found")
        page = session.get(PageRecord, generation.page_id)
        run = session.get(WorkflowRunRecord, page.run_id) if page else None
        artifact_id = (
            generation.page_image_artifact_id
            if asset == "image"
            else generation.thumbnail_artifact_id
        )
        if page is None or run is None or artifact_id is None:
            raise LookupError("page asset not found")
        return run.id, run.project_id, artifact_id
