"""Transactional persistence for workflow state and audit events."""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ppt_agent.domain.state_machine import RunEvent, RunStatus, transition_run
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ProjectRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)


class RecordNotFound(LookupError):
    """Raised when a requested runtime record does not exist."""


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
    def _append_event(
        session: Session,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> WorkflowEventRecord:
        next_sequence = session.scalar(
            select(func.coalesce(func.max(WorkflowEventRecord.run_seq), 0) + 1).where(
                WorkflowEventRecord.run_id == run_id
            )
        )
        record = WorkflowEventRecord(
            event_id=str(uuid4()),
            run_id=run_id,
            run_seq=int(next_sequence or 1),
            event_type=event_type,
            payload=payload,
        )
        session.add(record)
        return record

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
