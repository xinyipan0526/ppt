"""Authenticated HTTP boundary for the durable deck-planning workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ppt_agent.api.security import InputLimits, LocalHTTPSecurity
from ppt_agent.runtime.repository import InterruptConflict, RecordNotFound
from ppt_agent.tools.clarification import ClarificationValidationError
from ppt_agent.workflows.planning_store import PlanningStore


ProjectAuthorizer = Callable[[str], Awaitable[None]]
RunAuthorizer = Callable[[str], Awaitable[None]]


class PlanningWorkflow(Protocol):
    store: PlanningStore

    async def create_run(
        self,
        *,
        project_id: str,
        normalized_text: str,
        source_refs: tuple[str, ...] = (),
    ) -> Any: ...


class PlanningSupervisor(Protocol):
    def schedule(self, run_id: str) -> bool: ...


class PlanningRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    normalized_text: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()


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
        try:
            run = await workflow.create_run(
                project_id=project_id,
                normalized_text=request.normalized_text,
                source_refs=request.source_refs,
            )
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
            brief = await asyncio.to_thread(
                workflow.store.stage_output_refs, run_id, "build_brief"
            )
            outline = await asyncio.to_thread(
                workflow.store.stage_output_refs, run_id, "build_outline"
            )
            design = await asyncio.to_thread(
                workflow.store.stage_output_refs, run_id, "build_design_system"
            )
            pages = await asyncio.to_thread(
                workflow.store.stage_output_refs, run_id, "build_page_specs"
            )
            bundle = await asyncio.to_thread(
                workflow.store.stage_output_refs, run_id, "review_plan"
            )
        except (LookupError, RecordNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "run_id": run.id,
            "status": run.status.value,
            "input_generation": planning_input.generation,
            "brief_ref": _single_ref(brief),
            "outline_ref": _single_ref(outline),
            "design_system_ref": _single_ref(design),
            "page_spec_refs": list(pages),
            "bundle_ref": _single_ref(bundle),
        }

    return router


def _single_ref(refs: tuple[str, ...]) -> str | None:
    return refs[0] if len(refs) == 1 else None
