"""Authenticated project lifecycle and budget APIs."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.ledger.costs import CostLedger
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import BudgetAccountRecord, ProjectRecord, WorkflowRunRecord


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    budget_minor_units: int = Field(default=100_000, ge=0, le=1_000_000_000)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")


def projects_router(*, sessions: SessionFactory, security: LocalHTTPSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(request: ProjectCreate) -> dict:
        project_id = str(uuid4())
        with session_scope(sessions) as session:
            session.add(ProjectRecord(id=project_id, name=request.name))
        ledger = CostLedger(sessions)
        for stage in ("planning", "visual", "vision", "ocr"):
            ledger.create_account(project_id, stage, request.currency, request.budget_minor_units)
        return {"id": project_id, "name": request.name}

    @router.get("/projects")
    async def list_projects() -> list[dict]:
        with session_scope(sessions) as session:
            projects = list(session.scalars(select(ProjectRecord).order_by(ProjectRecord.created_at)))
            return [{"id": item.id, "name": item.name, "created_at": item.created_at} for item in projects]

    @router.get("/projects/{project_id}")
    async def get_project(project_id: str) -> dict:
        with session_scope(sessions) as session:
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            accounts = list(session.scalars(select(BudgetAccountRecord).where(BudgetAccountRecord.project_id == project_id)))
            return {
                "id": project.id, "name": project.name,
                "budgets": [{
                    "stage": item.stage, "currency": item.currency, "hard_limit": item.hard_limit,
                    "settled": item.settled, "active_reserved": item.active_reserved,
                    "held_unknown": item.held_unknown,
                } for item in accounts],
            }

    @router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project_id: str) -> None:
        with session_scope(sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            active = session.scalar(select(WorkflowRunRecord.id).where(WorkflowRunRecord.project_id == project_id).limit(1))
            if active is not None:
                raise HTTPException(status_code=409, detail="project with workflow history cannot be deleted")
            session.delete(project)

    return router
