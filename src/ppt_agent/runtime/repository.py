"""Transactional persistence for workflow state and audit events."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ppt_agent.domain.state_machine import (
    RunEvent,
    RunStatus,
    StepEvent,
    StepStatus,
    transition_run,
    transition_step,
)
from ppt_agent.events.persistence import append_event_record
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ProjectRecord,
    InterruptStatus,
    WorkflowInterruptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)


class RecordNotFound(LookupError):
    """Raised when a requested runtime record does not exist."""


class InterruptConflict(RuntimeError):
    """Raised when a Resume request conflicts with persisted interrupt state."""


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    id: str
    project_id: str
    workflow_name: str
    workflow_version: int
    status: RunStatus


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    event_id: str
    run_id: str
    run_seq: int
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StepSnapshot:
    id: str
    run_id: str
    step_key: str
    generation: int
    status: StepStatus


@dataclass(frozen=True, slots=True)
class InterruptSnapshot:
    id: str
    run_id: str
    step_id: str | None
    status: InterruptStatus
    version: int
    form_schema: dict[str, Any]
    resume_payload: dict[str, Any] | None


class WorkflowRepository:
    """Apply runtime state changes and their events in one transaction."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def create_project(self, name: str) -> str:
        project_id = str(uuid4())
        with session_scope(self._sessions) as session:
            session.add(ProjectRecord(id=project_id, name=name))
        return project_id

    def create_run(
        self,
        project_id: str,
        workflow_name: str,
        workflow_version: int,
    ) -> RunSnapshot:
        run_id = str(uuid4())
        with session_scope(self._sessions) as session:
            record = WorkflowRunRecord(
                id=run_id,
                project_id=project_id,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                status=RunStatus.QUEUED,
            )
            session.add(record)
            session.flush()
            self._append_event(
                session,
                run_id=run_id,
                event_type="run.queued",
                payload={"status": RunStatus.QUEUED.value},
            )
        return self._run_snapshot(record)

    def get_run(self, run_id: str) -> RunSnapshot:
        with session_scope(self._sessions) as session:
            record = self._require_run(session, run_id)
            return self._run_snapshot(record)

    def create_step(
        self,
        run_id: str,
        step_key: str,
        idempotency_key: str,
    ) -> StepSnapshot:
        step_id = str(uuid4())
        with session_scope(self._sessions) as session:
            self._require_run(session, run_id)
            record = WorkflowStepRecord(
                id=step_id,
                run_id=run_id,
                step_key=step_key,
                generation=1,
                status=StepStatus.PENDING,
                attempt_count=0,
                idempotency_key=idempotency_key,
            )
            session.add(record)
            session.flush()
            self._append_event(
                session,
                run_id=run_id,
                event_type="step.created",
                payload={
                    "step_id": step_id,
                    "step_key": step_key,
                    "status": StepStatus.PENDING.value,
                },
            )
        return self._step_snapshot(record)

    def get_step(self, step_id: str) -> StepSnapshot:
        with session_scope(self._sessions) as session:
            return self._step_snapshot(self._require_step(session, step_id))

    def transition_step(
        self,
        step_id: str,
        event: StepEvent,
        *,
        guard_satisfied: bool = True,
        payload: dict[str, Any] | None = None,
    ) -> StepSnapshot:
        with session_scope(self._sessions) as session:
            record = self._require_step(session, step_id)
            next_status = transition_step(
                record.status,
                event,
                guard_satisfied=guard_satisfied,
            )
            record.status = next_status
            event_payload = dict(payload or {})
            event_payload.update({"step_id": step_id, "status": next_status.value})
            self._append_event(
                session,
                run_id=record.run_id,
                event_type=f"step.{event.value}",
                payload=event_payload,
            )
            return self._step_snapshot(record)

    def create_interrupt(
        self,
        *,
        run_id: str,
        step_id: str,
        form_schema: dict[str, Any],
    ) -> InterruptSnapshot:
        interrupt_id = str(uuid4())
        with session_scope(self._sessions) as session:
            run = self._require_run(session, run_id)
            step = self._require_step(session, step_id)
            if step.run_id != run_id:
                raise InterruptConflict("step does not belong to run")

            run.status = transition_run(run.status, RunEvent.INTERRUPT_CREATED)
            step.status = transition_step(step.status, StepEvent.INTERRUPTED)
            record = WorkflowInterruptRecord(
                id=interrupt_id,
                run_id=run_id,
                step_id=step_id,
                page_id=None,
                status=InterruptStatus.PENDING,
                version=1,
                form_schema=form_schema,
            )
            session.add(record)
            self._append_event(
                session,
                run_id=run_id,
                event_type="interrupt.created",
                payload={
                    "interrupt_id": interrupt_id,
                    "step_id": step_id,
                    "run_status": run.status.value,
                    "step_status": step.status.value,
                },
            )
            return self._interrupt_snapshot(record)

    def get_interrupt(self, interrupt_id: str) -> InterruptSnapshot:
        with session_scope(self._sessions) as session:
            return self._interrupt_snapshot(
                self._require_interrupt(session, interrupt_id)
            )

    def resume_interrupt(
        self,
        interrupt_id: str,
        *,
        expected_version: int,
        payload: dict[str, Any],
    ) -> InterruptSnapshot:
        with session_scope(self._sessions) as session:
            interrupt = self._require_interrupt(session, interrupt_id)
            if interrupt.version != expected_version:
                raise InterruptConflict("interrupt version mismatch")
            if interrupt.status is InterruptStatus.RESOLVED:
                if interrupt.resume_payload != payload:
                    raise InterruptConflict(
                        "interrupt already resolved with different payload"
                    )
                return self._interrupt_snapshot(interrupt)
            if interrupt.step_id is None:
                raise InterruptConflict("interrupt has no associated step")

            run = self._require_run(session, interrupt.run_id)
            step = self._require_step(session, interrupt.step_id)
            run.status = transition_run(run.status, RunEvent.INTERRUPT_RESUMED)
            step.status = transition_step(step.status, StepEvent.INTERRUPT_RESUMED)
            step.generation += 1
            interrupt.status = InterruptStatus.RESOLVED
            interrupt.resume_payload = payload
            interrupt.resolved_at = datetime.now(UTC)
            self._append_event(
                session,
                run_id=interrupt.run_id,
                event_type="interrupt.resumed",
                payload={
                    "interrupt_id": interrupt_id,
                    "step_id": step.id,
                    "step_generation": step.generation,
                    "run_status": run.status.value,
                    "step_status": step.status.value,
                },
            )
            return self._interrupt_snapshot(interrupt)

    def transition_run(
        self,
        run_id: str,
        event: RunEvent,
        *,
        guard_satisfied: bool = True,
        payload: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        with session_scope(self._sessions) as session:
            record = self._require_run(session, run_id)
            next_status = transition_run(
                record.status,
                event,
                guard_satisfied=guard_satisfied,
            )
            record.status = next_status
            event_payload = dict(payload or {})
            event_payload["status"] = next_status.value
            self._append_event(
                session,
                run_id=run_id,
                event_type=f"run.{event.value}",
                payload=event_payload,
            )
            return self._run_snapshot(record)

    def list_events(self, run_id: str) -> list[EventSnapshot]:
        with session_scope(self._sessions) as session:
            records = session.scalars(
                select(WorkflowEventRecord)
                .where(WorkflowEventRecord.run_id == run_id)
                .order_by(WorkflowEventRecord.run_seq)
            ).all()
            return [self._event_snapshot(record) for record in records]

    @staticmethod
    def _require_run(session: Session, run_id: str) -> WorkflowRunRecord:
        record = session.get(WorkflowRunRecord, run_id)
        if record is None:
            raise RecordNotFound(f"workflow run not found: {run_id}")
        return record

    @staticmethod
    def _require_step(session: Session, step_id: str) -> WorkflowStepRecord:
        record = session.get(WorkflowStepRecord, step_id)
        if record is None:
            raise RecordNotFound(f"workflow step not found: {step_id}")
        return record

    @staticmethod
    def _require_interrupt(
        session: Session, interrupt_id: str
    ) -> WorkflowInterruptRecord:
        record = session.get(WorkflowInterruptRecord, interrupt_id)
        if record is None:
            raise RecordNotFound(f"workflow interrupt not found: {interrupt_id}")
        return record

    @staticmethod
    def _append_event(
        session: Session,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> WorkflowEventRecord:
        return append_event_record(
            session,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def _run_snapshot(record: WorkflowRunRecord) -> RunSnapshot:
        return RunSnapshot(
            id=record.id,
            project_id=record.project_id,
            workflow_name=record.workflow_name,
            workflow_version=record.workflow_version,
            status=record.status,
        )

    @staticmethod
    def _event_snapshot(record: WorkflowEventRecord) -> EventSnapshot:
        return EventSnapshot(
            event_id=record.event_id,
            run_id=record.run_id,
            run_seq=record.run_seq,
            event_type=record.event_type,
            payload=record.payload,
        )

    @staticmethod
    def _step_snapshot(record: WorkflowStepRecord) -> StepSnapshot:
        return StepSnapshot(
            id=record.id,
            run_id=record.run_id,
            step_key=record.step_key,
            generation=record.generation,
            status=record.status,
        )

    @staticmethod
    def _interrupt_snapshot(record: WorkflowInterruptRecord) -> InterruptSnapshot:
        return InterruptSnapshot(
            id=record.id,
            run_id=record.run_id,
            step_id=record.step_id,
            status=record.status,
            version=record.version,
            form_schema=record.form_schema,
            resume_payload=record.resume_payload,
        )
