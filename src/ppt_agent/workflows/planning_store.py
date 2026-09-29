"""Transactional planning input and stage persistence."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import func, select, text

from ppt_agent.domain.contracts import ContractModel
from ppt_agent.domain.artifacts import CONTENT_DEPENDENCY_EDGES, ArtifactStatus
from ppt_agent.domain.materials import MaterialStatus
from ppt_agent.domain.state_machine import (
    PageEvent,
    PageStatus,
    RunEvent,
    RunStatus,
    StepEvent,
    StepStatus,
    transition_page,
    transition_run,
    transition_step,
)
from ppt_agent.events.persistence import append_event_record
from ppt_agent.runtime.repository import RunSnapshot, StepSnapshot, WorkflowRepository
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    InterruptStatus,
    ArtifactEdgeRecord,
    ArtifactRecord,
    PageRecord,
    PlanningInputRecord,
    ProjectRecord,
    SourceMaterialRecord,
    WorkflowRunRecord,
    WorkflowInterruptRecord,
    WorkflowStepRecord,
)


PlanningStage = Literal[
    "build_brief",
    "build_outline",
    "build_design_system",
    "build_page_specs",
    "review_plan",
]
PLANNING_STAGES: tuple[PlanningStage, ...] = (
    "build_brief",
    "build_outline",
    "build_design_system",
    "build_page_specs",
    "review_plan",
)


@dataclass(frozen=True, slots=True)
class PlanningInputSnapshot:
    id: str
    run_id: str
    generation: int
    normalized_text: str
    source_refs: tuple[str, ...]
    feedback: str | None
    revision_scope: str | None
    page_indexes: tuple[int, ...]
    clarification_answers: dict | None


@dataclass(frozen=True, slots=True)
class PlanningStep:
    stage: PlanningStage
    step_id: str
    generation: int
    input_refs: tuple[str, ...]


class PlanRevisionRequest(ContractModel):
    action: Literal["revise"] = "revise"
    feedback: str = Field(min_length=1, max_length=4_000)
    scope: Literal["brief", "outline", "design", "pages", "auto"]
    page_indexes: tuple[int, ...] = ()

    @model_validator(mode="after")
    def validate_page_indexes(self) -> "PlanRevisionRequest":
        if len(self.page_indexes) != len(set(self.page_indexes)):
            raise ValueError("duplicate page indexes")
        if any(index < 1 or index > 50 for index in self.page_indexes):
            raise ValueError("page index outside range 1..50")
        if self.scope != "pages" and self.page_indexes:
            raise ValueError("page indexes require pages scope")
        return self


class PlanningStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self.repository = WorkflowRepository(sessions)

    def create_run(
        self,
        project_id: str,
        source_refs: tuple[str, ...],
        normalized_text: str,
        *,
        validate_source_refs: bool = False,
        source_material_refs: tuple[tuple[str, str], ...] = (),
    ) -> RunSnapshot:
        if not normalized_text.strip():
            raise ValueError("normalized planning text must not be empty")
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("source artifact references must be unique")
        run_id = str(uuid4())
        with session_scope(self._sessions) as session:
            if validate_source_refs:
                session.execute(text("BEGIN IMMEDIATE"))
            if session.get(ProjectRecord, project_id) is None:
                raise LookupError(f"project not found: {project_id}")
            if validate_source_refs:
                for artifact_id in source_refs:
                    artifact = session.get(ArtifactRecord, artifact_id)
                    if (
                        artifact is None
                        or artifact.project_id != project_id
                        or artifact.kind != "parsed_material"
                        or artifact.status is not ArtifactStatus.AVAILABLE
                    ):
                        raise ValueError(
                            f"source artifact is not an available parsed material: {artifact_id}"
                        )
                for material_id, artifact_id in source_material_refs:
                    material = session.get(SourceMaterialRecord, material_id)
                    if (
                        material is None
                        or material.project_id != project_id
                        or material.status is not MaterialStatus.READY
                        or material.parsed_artifact_id != artifact_id
                    ):
                        raise ValueError(
                            f"source material is no longer ready: {material_id}"
                        )
            run = WorkflowRunRecord(
                id=run_id,
                project_id=project_id,
                workflow_name="deck_planning",
                workflow_version=1,
                status=RunStatus.QUEUED,
            )
            session.add(run)
            # No ORM relationship links these records, so establish the FK parent
            # before SQLAlchemy is free to order the independent INSERTs.
            session.flush()
            session.add(
                PlanningInputRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    generation=1,
                    normalized_text=normalized_text.strip(),
                    source_refs=list(source_refs),
                    feedback=None,
                    revision_scope=None,
                    page_indexes=[],
                    clarification_answers=None,
                )
            )
            session.flush()
            append_event_record(
                session,
                run_id=run_id,
                event_type="run.queued",
                payload={"status": RunStatus.QUEUED.value, "workflow": "deck_planning/v1"},
            )
            return RunSnapshot(
                id=run.id,
                project_id=run.project_id,
                workflow_name=run.workflow_name,
                workflow_version=run.workflow_version,
                status=run.status,
            )

    def current_input(self, run_id: str) -> PlanningInputSnapshot:
        with session_scope(self._sessions) as session:
            record = session.scalar(
                select(PlanningInputRecord)
                .where(PlanningInputRecord.run_id == run_id)
                .order_by(PlanningInputRecord.generation.desc())
            )
            if record is None:
                raise LookupError(f"planning input not found: {run_id}")
            return self._input_snapshot(record)

    def next_step(self, run_id: str) -> PlanningStep:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError(f"run not found: {run_id}")
            current = session.scalar(
                select(PlanningInputRecord)
                .where(PlanningInputRecord.run_id == run_id)
                .order_by(PlanningInputRecord.generation.desc())
            )
            if current is None:
                raise LookupError(f"planning input not found: {run_id}")
            for stage in PLANNING_STAGES:
                step = session.scalar(
                    select(WorkflowStepRecord)
                    .where(
                        WorkflowStepRecord.run_id == run_id,
                        WorkflowStepRecord.step_key == stage,
                    )
                    .order_by(WorkflowStepRecord.generation.desc())
                )
                if step is not None:
                    if step.status is StepStatus.SUCCEEDED:
                        continue
                    return self._planning_step(step)
                step = WorkflowStepRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    step_key=stage,
                    generation=1,
                    status=StepStatus.PENDING,
                    attempt_count=0,
                    idempotency_key=f"deck-planning:{run_id}:{stage}:1",
                    input_refs=self._previous_output_refs(session, run_id, stage),
                )
                session.add(step)
                session.flush()
                append_event_record(
                    session,
                    run_id=run_id,
                    event_type="step.created",
                    step_id=step.id,
                    payload={"step_id": step.id, "step_key": stage, "generation": 1},
                )
                return self._planning_step(step)
        raise RuntimeError("planning run has no remaining step")

    def complete_step(
        self, step_id: str, output_refs: tuple[str, ...]
    ) -> StepSnapshot:
        with session_scope(self._sessions) as session:
            step = session.get(WorkflowStepRecord, step_id)
            if step is None:
                raise LookupError(f"step not found: {step_id}")
            if step.status is not StepStatus.RUNNING:
                raise ValueError("only a running step can complete")
            step.status = StepStatus.SUCCEEDED
            step.output_refs = list(output_refs)
            run = session.get(WorkflowRunRecord, step.run_id)
            if run is None:
                raise LookupError(f"run not found: {step.run_id}")
            if step.step_key == "build_brief" and output_refs:
                run.brief_version_id = output_refs[0]
            append_event_record(
                session,
                run_id=step.run_id,
                event_type="step.completed",
                step_id=step.id,
                payload={
                    "step_id": step.id,
                    "step_key": step.step_key,
                    "generation": step.generation,
                    "output_refs": list(output_refs),
                },
            )
            return StepSnapshot(
                id=step.id,
                run_id=step.run_id,
                step_key=step.step_key,
                generation=step.generation,
                status=step.status,
            )

    def complete_page_specs(
        self, step_id: str, output_refs: tuple[str, ...]
    ) -> StepSnapshot:
        with session_scope(self._sessions) as session:
            step = session.get(WorkflowStepRecord, step_id)
            if step is None:
                raise LookupError(f"step not found: {step_id}")
            if step.status is not StepStatus.RUNNING:
                raise ValueError("only a running step can complete")
            for page_index, artifact_id in enumerate(output_refs, start=1):
                page = session.scalar(
                    select(PageRecord).where(
                        PageRecord.run_id == step.run_id,
                        PageRecord.page_index == page_index,
                    )
                )
                if page is None:
                    page = PageRecord(
                        id=str(uuid4()),
                        run_id=step.run_id,
                        page_index=page_index,
                        status=PageStatus.PLANNED,
                    )
                    session.add(page)
                page.page_spec_version_id = artifact_id
            step.status = StepStatus.SUCCEEDED
            step.output_refs = list(output_refs)
            append_event_record(
                session,
                run_id=step.run_id,
                event_type="step.completed",
                step_id=step.id,
                payload={
                    "step_id": step.id,
                    "step_key": step.step_key,
                    "generation": step.generation,
                    "output_refs": list(output_refs),
                },
            )
            return StepSnapshot(
                id=step.id,
                run_id=step.run_id,
                step_key=step.step_key,
                generation=step.generation,
                status=step.status,
            )

    def stage_output_refs(self, run_id: str, stage: PlanningStage) -> tuple[str, ...]:
        with session_scope(self._sessions) as session:
            step = session.scalar(
                select(WorkflowStepRecord)
                .where(
                    WorkflowStepRecord.run_id == run_id,
                    WorkflowStepRecord.step_key == stage,
                    WorkflowStepRecord.status == StepStatus.SUCCEEDED,
                )
                .order_by(WorkflowStepRecord.generation.desc())
            )
            return () if step is None else tuple(step.output_refs or ())

    def available_stage_output_refs(
        self, run_id: str, stage: PlanningStage
    ) -> tuple[str, ...]:
        refs = self.stage_output_refs(run_id, stage)
        if not refs:
            return ()
        with session_scope(self._sessions) as session:
            records = [session.get(ArtifactRecord, artifact_id) for artifact_id in refs]
            if any(
                record is None or record.status is not ArtifactStatus.AVAILABLE
                for record in records
            ):
                return ()
        return refs

    def step_resume_payload(self, step_id: str) -> dict | None:
        with session_scope(self._sessions) as session:
            interrupt = session.scalar(
                select(WorkflowInterruptRecord)
                .where(
                    WorkflowInterruptRecord.step_id == step_id,
                    WorkflowInterruptRecord.status == InterruptStatus.RESOLVED,
                )
                .order_by(WorkflowInterruptRecord.resolved_at.desc())
            )
            return None if interrupt is None else dict(interrupt.resume_payload or {})

    def record_clarification(
        self, run_id: str, answers: dict
    ) -> PlanningInputSnapshot:
        with session_scope(self._sessions) as session:
            current = session.scalar(
                select(PlanningInputRecord)
                .where(PlanningInputRecord.run_id == run_id)
                .order_by(PlanningInputRecord.generation.desc())
            )
            if current is None:
                raise LookupError(f"planning input not found: {run_id}")
            merged = {**(current.clarification_answers or {}), **answers}
            if current.clarification_answers == merged:
                return self._input_snapshot(current)
            record = PlanningInputRecord(
                id=str(uuid4()),
                run_id=run_id,
                generation=current.generation + 1,
                normalized_text=current.normalized_text,
                source_refs=list(current.source_refs),
                feedback=None,
                revision_scope=None,
                page_indexes=[],
                clarification_answers=merged,
            )
            session.add(record)
            session.flush()
            append_event_record(
                session,
                run_id=run_id,
                event_type="planning.clarification_recorded",
                payload={
                    "input_generation": record.generation,
                    "answer_fields": sorted(merged),
                },
            )
            return self._input_snapshot(record)

    def finish_run(self, run_id: str, result_refs: tuple[str, ...]) -> None:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError(f"run not found: {run_id}")
            if run.status is not RunStatus.RUNNING:
                raise ValueError("only a running run can finish")
            run.status = RunStatus.SUCCEEDED
            run.result_refs = list(result_refs)
            append_event_record(
                session,
                run_id=run_id,
                event_type="run.succeeded",
                payload={"status": RunStatus.SUCCEEDED.value, "result_refs": list(result_refs)},
            )

    def finish_review(
        self,
        run_id: str,
        step_id: str,
        bundle_id: str,
        result_refs: tuple[str, ...],
    ) -> None:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            step = session.get(WorkflowStepRecord, step_id)
            if run is None or step is None or step.run_id != run_id:
                raise LookupError("planning review state not found")
            if run.status is not RunStatus.RUNNING or step.status is not StepStatus.RUNNING:
                raise ValueError("planning review is not running")
            step.status = transition_step(step.status, StepEvent.COMPLETED)
            step.output_refs = [bundle_id]
            run.status = transition_run(
                run.status, RunEvent.ALL_REQUIRED_STEPS_SUCCEEDED
            )
            run.result_refs = [bundle_id, *result_refs]
            for page in session.scalars(
                select(PageRecord).where(PageRecord.run_id == run_id)
            ):
                if page.status is PageStatus.PLANNED:
                    page.status = transition_page(page.status, PageEvent.APPROVED)
            append_event_record(
                session,
                run_id=run_id,
                event_type="planning.approved",
                step_id=step_id,
                payload={"bundle_ref": bundle_id, "status": RunStatus.SUCCEEDED.value},
            )

    def cancel_review(self, run_id: str, step_id: str) -> None:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            step = session.get(WorkflowStepRecord, step_id)
            if run is None or step is None or step.run_id != run_id:
                raise LookupError("planning review state not found")
            if run.status is not RunStatus.RUNNING or step.status is not StepStatus.RUNNING:
                raise ValueError("planning review is not running")
            step.status = transition_step(step.status, StepEvent.CANCEL_REQUESTED)
            step.status = transition_step(
                step.status, StepEvent.CANCELLATION_COMPLETED
            )
            run.status = transition_run(run.status, RunEvent.CANCEL_REQUESTED)
            run.status = transition_run(
                run.status, RunEvent.CANCELLATION_COMPLETED
            )
            append_event_record(
                session,
                run_id=run_id,
                event_type="planning.cancelled",
                step_id=step_id,
                payload={"status": RunStatus.CANCELLED.value},
            )

    def apply_revision(
        self,
        run_id: str,
        request: PlanRevisionRequest,
        *,
        review_step_id: str | None = None,
        stale_roots: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        start_by_scope = {
            "brief": 0,
            "outline": 1,
            "design": 2,
            "pages": 3,
            "auto": 1,
        }
        affected = PLANNING_STAGES[start_by_scope[request.scope] :]
        with session_scope(self._sessions) as session:
            current = session.scalar(
                select(PlanningInputRecord)
                .where(PlanningInputRecord.run_id == run_id)
                .order_by(PlanningInputRecord.generation.desc())
            )
            if current is None:
                raise LookupError(f"planning input not found: {run_id}")
            if review_step_id is not None:
                review = session.get(WorkflowStepRecord, review_step_id)
                if (
                    review is None
                    or review.run_id != run_id
                    or review.status is not StepStatus.RUNNING
                ):
                    raise ValueError("planning review is not running")
                review.status = StepStatus.SUCCEEDED
                review.output_refs = []
            self._mark_stale_in_session(session, stale_roots)
            session.add(
                PlanningInputRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    generation=current.generation + 1,
                    normalized_text=current.normalized_text,
                    source_refs=list(current.source_refs),
                    feedback=request.feedback,
                    revision_scope=request.scope,
                    page_indexes=list(request.page_indexes),
                    clarification_answers=current.clarification_answers,
                )
            )
            for stage in affected:
                generation = (
                    session.scalar(
                        select(func.max(WorkflowStepRecord.generation)).where(
                            WorkflowStepRecord.run_id == run_id,
                            WorkflowStepRecord.step_key == stage,
                        )
                    )
                    or 0
                ) + 1
                step = WorkflowStepRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    step_key=stage,
                    generation=generation,
                    status=StepStatus.PENDING,
                    attempt_count=0,
                    idempotency_key=(
                        f"deck-planning:{run_id}:{stage}:{generation}"
                    ),
                    input_refs=self._previous_output_refs(session, run_id, stage),
                )
                session.add(step)
            append_event_record(
                session,
                run_id=run_id,
                event_type="planning.revision_requested",
                payload={
                    "scope": request.scope,
                    "page_indexes": list(request.page_indexes),
                    "input_generation": current.generation + 1,
                },
            )
        return tuple(affected)

    @staticmethod
    def _mark_stale_in_session(session, roots: tuple[str, ...]) -> None:
        pending = deque(roots)
        seen: set[str] = set()
        now = datetime.now(UTC)
        while pending:
            artifact_id = pending.popleft()
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            artifact = session.get(ArtifactRecord, artifact_id)
            if artifact is None:
                raise LookupError(f"artifact not found: {artifact_id}")
            if artifact.status is ArtifactStatus.AVAILABLE:
                artifact.status = ArtifactStatus.STALE
                artifact.retention_since = now
            pending.extend(
                session.scalars(
                    select(ArtifactEdgeRecord.target_artifact_id).where(
                        ArtifactEdgeRecord.source_artifact_id == artifact_id,
                        ArtifactEdgeRecord.edge_type.in_(CONTENT_DEPENDENCY_EDGES),
                    )
                )
            )

    @staticmethod
    def _previous_output_refs(session, run_id: str, stage: str) -> list[str]:
        index = PLANNING_STAGES.index(stage)  # type: ignore[arg-type]
        if index == 0:
            current = session.scalar(
                select(PlanningInputRecord)
                .where(PlanningInputRecord.run_id == run_id)
                .order_by(PlanningInputRecord.generation.desc())
            )
            return [] if current is None else list(current.source_refs or ())
        previous = session.scalar(
            select(WorkflowStepRecord)
            .where(
                WorkflowStepRecord.run_id == run_id,
                WorkflowStepRecord.step_key == PLANNING_STAGES[index - 1],
                WorkflowStepRecord.status == StepStatus.SUCCEEDED,
            )
            .order_by(WorkflowStepRecord.generation.desc())
        )
        return [] if previous is None else list(previous.output_refs or ())

    @staticmethod
    def _planning_step(record: WorkflowStepRecord) -> PlanningStep:
        return PlanningStep(
            stage=record.step_key,  # type: ignore[arg-type]
            step_id=record.id,
            generation=record.generation,
            input_refs=tuple(record.input_refs or ()),
        )

    @staticmethod
    def _input_snapshot(record: PlanningInputRecord) -> PlanningInputSnapshot:
        return PlanningInputSnapshot(
            id=record.id,
            run_id=record.run_id,
            generation=record.generation,
            normalized_text=record.normalized_text,
            source_refs=tuple(record.source_refs),
            feedback=record.feedback,
            revision_scope=record.revision_scope,
            page_indexes=tuple(record.page_indexes),
            clarification_answers=(
                None
                if record.clarification_answers is None
                else dict(record.clarification_answers)
            ),
        )
