"""Authenticated HTTP boundary for the durable deck-planning workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.materials import ParsedMaterialV1
from ppt_agent.materials.service import MaterialService
from ppt_agent.materials.store import MaterialConflict, MaterialNotFound, ResolvedMaterial
from ppt_agent.runtime.repository import InterruptConflict, RecordNotFound
from ppt_agent.tools.clarification import ClarificationValidationError
from ppt_agent.workflows.planning_store import PlanningStore


ProjectAuthorizer = Callable[[str], Awaitable[None]]
RunAuthorizer = Callable[[str], Awaitable[None]]


class PlanningSourceTooLarge(ValueError):
    pass


class PlanningWorkflow(Protocol):
    store: PlanningStore
    artifacts: ArtifactStore

    async def create_run(
        self,
        *,
        project_id: str,
        normalized_text: str,
        source_refs: tuple[str, ...] = (),
        validate_source_refs: bool = False,
        source_material_refs: tuple[tuple[str, str], ...] = (),
    ) -> Any: ...


class PlanningSupervisor(Protocol):
    def schedule(self, run_id: str) -> bool: ...


class PlanningRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    normalized_text: str = ""
    material_ids: tuple[str, ...] = Field(default=(), max_length=100)


class InterruptResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    payload: dict[str, Any]


def planning_router(
    *,
    workflow: PlanningWorkflow,
    supervisor: PlanningSupervisor,
    authorize_project: ProjectAuthorizer,
    authorize_run: RunAuthorizer,
    security: LocalHTTPSecurity,
    input_limits: InputLimits,
    material_service: MaterialService | None = None,
) -> APIRouter:
    """Build routes without weakening transport or resource ownership checks."""

    router = APIRouter(prefix="/api/v1")
    authenticated = [Depends(security.authenticate)]

    @router.post(
        "/projects/{project_id}/planning-runs",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    async def create_planning_run(
        project_id: str, request: PlanningRunCreate
    ) -> dict[str, Any]:
        await authorize_project(project_id)
        if len(request.normalized_text) > input_limits.max_markdown_characters:
            raise HTTPException(status_code=413, detail="planning input is too large")
        if not request.normalized_text and not request.material_ids:
            raise HTTPException(
                status_code=422,
                detail="planning input requires text or at least one material",
            )
        try:
            resolved: tuple[ResolvedMaterial, ...] = ()
            if request.material_ids:
                if material_service is None:
                    raise HTTPException(
                        status_code=503, detail="material ingestion is not configured"
                    )
                resolved = await asyncio.to_thread(
                    material_service.store.resolve_ready,
                    project_id,
                    request.material_ids,
                )
            compiled_text, source_refs = await asyncio.to_thread(
                _compile_planning_sources,
                request.normalized_text,
                resolved,
                material_service,
                input_limits.max_planning_source_bytes,
            )
            run = await workflow.create_run(
                project_id=project_id,
                normalized_text=compiled_text,
                source_refs=source_refs,
                validate_source_refs=bool(source_refs),
                source_material_refs=tuple(
                    (item.material.id, item.parsed_artifact.id) for item in resolved
                ),
            )
        except MaterialNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MaterialConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanningSourceTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        supervisor.schedule(run.id)
        return {"run_id": run.id, "status": run.status.value}

    @router.post(
        "/runs/{run_id}/interrupts/{interrupt_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    async def resume_planning_run(
        run_id: str, interrupt_id: str, request: InterruptResume
    ) -> dict[str, Any]:
        await authorize_run(run_id)
        try:
            interrupt = await asyncio.to_thread(
                workflow.store.repository.get_interrupt, interrupt_id
            )
            if interrupt.run_id != run_id:
                raise HTTPException(status_code=404, detail="interrupt not found")
            resumed = await asyncio.to_thread(
                workflow.store.repository.resume_interrupt,
                interrupt_id,
                expected_version=request.expected_version,
                payload=request.payload,
            )
        except (LookupError, RecordNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InterruptConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ClarificationValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        supervisor.schedule(run_id)
        return {
            "run_id": run_id,
            "interrupt_id": resumed.id,
            "status": resumed.status.value,
        }

    @router.get(
        "/runs/{run_id}/planning",
        dependencies=authenticated,
    )
    async def planning_projection(run_id: str) -> dict[str, Any]:
        await authorize_run(run_id)
        try:
            run = await asyncio.to_thread(workflow.store.repository.get_run, run_id)
            planning_input = await asyncio.to_thread(workflow.store.current_input, run_id)
            refs = await asyncio.to_thread(
                workflow.store.available_planning_refs, run_id
            )
        except (LookupError, RecordNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "run_id": run.id,
            "status": run.status.value,
            "error": run.error,
            "input_generation": planning_input.generation,
            "brief_ref": _single_ref(refs.brief),
            "outline_ref": _single_ref(refs.outline),
            "design_system_ref": _single_ref(refs.design),
            "page_spec_refs": list(refs.pages),
            "bundle_ref": _single_ref(refs.bundle),
        }

    @router.get("/runs/{run_id}/plan-detail", dependencies=authenticated)
    async def planning_detail(run_id: str) -> dict[str, Any]:
        await authorize_run(run_id)
        artifacts = getattr(workflow, "artifacts", None)
        if artifacts is None:
            raise HTTPException(status_code=503, detail="planning artifacts are unavailable")
        try:
            refs = await asyncio.to_thread(
                workflow.store.available_planning_refs, run_id
            )
            return {
                "run_id": run_id,
                "brief": _read_planning_payload(artifacts, _single_ref(refs.brief)),
                "outline": _read_planning_payload(artifacts, _single_ref(refs.outline)),
                "design_system": _read_planning_payload(
                    artifacts, _single_ref(refs.design)
                ),
                "pages": [
                    _read_planning_payload(artifacts, item) for item in refs.pages
                ],
                "review_ready": (
                    len(refs.brief) == 1
                    and len(refs.outline) == 1
                    and len(refs.design) == 1
                    and len(refs.pages) > 0
                ),
                "bundle_ready": len(refs.bundle) == 1,
            }
        except (LookupError, RecordNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def _single_ref(refs: tuple[str, ...]) -> str | None:
    return refs[0] if len(refs) == 1 else None


def _read_planning_payload(
    artifacts: ArtifactStore, artifact_id: str | None
) -> dict[str, Any] | None:
    if artifact_id is None:
        return None
    value = json.loads(artifacts.read_bytes(artifact_id))
    if not isinstance(value, dict):
        raise ValueError("planning artifact must contain a JSON object")
    payload = value.get("payload", value)
    if not isinstance(payload, dict):
        raise ValueError("planning artifact payload must be an object")
    return _without_internal_artifact_refs(payload)


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


def _compile_planning_sources(
    normalized_text: str,
    materials: tuple[ResolvedMaterial, ...],
    material_service: MaterialService | None,
    max_bytes: int,
) -> tuple[str, tuple[str, ...]]:
    """Compile trusted transport fields and untrusted parsed blocks deterministically."""

    encoded_records: list[bytes] = []
    encoded_size = 0

    def append(record: dict[str, Any]) -> None:
        nonlocal encoded_size
        encoded = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        encoded_size += len(encoded) + (1 if encoded_records else 0)
        if encoded_size > max_bytes:
            raise PlanningSourceTooLarge(
                "compiled planning sources exceed the aggregate byte limit"
            )
        encoded_records.append(encoded)

    if normalized_text:
        append({"kind": "typed_user_input", "text": normalized_text})
    if materials and material_service is None:
        raise RuntimeError("material service is required to compile materials")
    for resolved in materials:
        payload = material_service.artifacts.read_bytes(resolved.parsed_artifact.id)  # type: ignore[union-attr]
        parsed = ParsedMaterialV1.model_validate_json(payload)
        if (
            parsed.material_id != resolved.material.id
            or parsed.source_artifact_id != resolved.material.source_artifact_id
        ):
            raise ValueError(
                f"parsed material identity does not match: {resolved.material.id}"
            )
        if parsed.text_blocks:
            for block in parsed.text_blocks:
                append(
                    {
                        "kind": "untrusted_source_content",
                        "locator": block.locator.model_dump(mode="json", exclude_none=True),
                        "material_id": parsed.material_id,
                        "media_type": parsed.media_type,
                        "source_artifact_id": parsed.source_artifact_id,
                        "text": block.text,
                    }
                )
        else:
            append(
                {
                    "kind": "untrusted_source_content",
                    "material_id": parsed.material_id,
                    "media_type": parsed.media_type,
                    "metadata": parsed.metadata.model_dump(mode="json", exclude_none=True),
                    "visual_analysis": parsed.visual_analysis.model_dump(mode="json") if parsed.visual_analysis else None,
                    "source_artifact_id": parsed.source_artifact_id,
                    "text": "",
                }
            )
    compiled = b"\n".join(encoded_records).decode("utf-8")
    return compiled, tuple(item.parsed_artifact.id for item in materials)
