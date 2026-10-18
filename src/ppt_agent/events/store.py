"""Persistence API for replayable workflow events and consistent snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from ppt_agent.domain.events import EventDurability
from ppt_agent.events.persistence import append_event_record
from ppt_agent.events.policy import EventPayloadRejected
from ppt_agent.operations.provider import safe_provider_failure_error
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    PageRecord,
    WorkflowEventRecord,
    WorkflowInterruptRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)


class CursorExpired(ValueError):
    """Raised when a cursor cannot describe a valid point in a Run stream."""


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    run_id: str
    run_seq: int
    event_type: str
    schema_version: int
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, Any]
    durability: EventDurability
    step_id: str | None
    page_id: str | None
    operation_id: str | None
    trace_id: str | None


@dataclass(frozen=True, slots=True)
class RunView:
    id: str
    project_id: str
    workflow_name: str
    workflow_version: int
    status: str
    error: dict[str, object] | None
    retry_at: datetime | None
    recovery_metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class EntityView:
    id: str
    status: str
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunStateSnapshot:
    run: RunView
    steps: tuple[EntityView, ...]
    pages: tuple[EntityView, ...]
    interrupts: tuple[EntityView, ...]
    snapshot_seq: int


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    events: tuple[EventEnvelope, ...]
    next_cursor: int
    cursor_expired: bool


class WorkflowEventStore:
    """Append, snapshot, replay, and retain workflow projection events."""

    def __init__(self, sessions: SessionFactory, *, max_payload_bytes: int = 65_536):
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._sessions = sessions
        self._max_payload_bytes = max_payload_bytes

    def append(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        durability: EventDurability,
        schema_version: int = 1,
        occurred_at: datetime | None = None,
        step_id: str | None = None,
        page_id: str | None = None,
        operation_id: str | None = None,
        trace_id: str | None = None,
    ) -> EventEnvelope:
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        with session_scope(self._sessions) as session:
            record = append_event_record(
                session,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                schema_version=schema_version,
                occurred_at=occurred_at,
                step_id=step_id,
                page_id=page_id,
                operation_id=operation_id,
                trace_id=trace_id,
                durability=durability,
                max_payload_bytes=self._max_payload_bytes,
            )
            session.flush()
            return self._event(record)

    def get_run_snapshot(self, run_id: str) -> RunStateSnapshot:
        with session_scope(self._sessions) as session:
            self._begin_consistent_read(session)
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError(f"workflow run not found: {run_id}")
            steps = session.scalars(
                select(WorkflowStepRecord)
                .where(WorkflowStepRecord.run_id == run_id)
                .order_by(WorkflowStepRecord.created_at, WorkflowStepRecord.id)
            ).all()
            pages = session.scalars(
                select(PageRecord)
                .where(PageRecord.run_id == run_id)
                .order_by(PageRecord.page_index)
            ).all()
            interrupts = session.scalars(
                select(WorkflowInterruptRecord)
                .where(WorkflowInterruptRecord.run_id == run_id)
                .order_by(
                    WorkflowInterruptRecord.created_at,
                    WorkflowInterruptRecord.id,
                )
            ).all()
            return RunStateSnapshot(
                run=RunView(
                    id=run.id,
                    project_id=run.project_id,
                    workflow_name=run.workflow_name,
                    workflow_version=run.workflow_version,
                    status=run.status.value,
                    error=(
                        None
                        if run.error is None
                        else safe_provider_failure_error(run.error)
                    ),
                    retry_at=run.retry_at,
                    recovery_metadata=run.recovery_metadata,
                ),
                steps=tuple(
                    EntityView(
                        id=step.id,
                        status=step.status.value,
                        values={
                            "step_key": step.step_key,
                            "generation": step.generation,
                            "attempt_count": step.attempt_count,
                            "input_refs": step.input_refs,
                            "output_refs": step.output_refs,
                            "error": (
                                None
                                if step.error is None
                                else safe_provider_failure_error(step.error)
                            ),
                            "retry_at": step.retry_at,
                            "recovery_metadata": step.recovery_metadata,
                        },
                    )
                    for step in steps
                ),
                pages=tuple(
                    EntityView(
                        id=page.id,
                        status=page.status.value,
                        values={
                            "page_index": page.page_index,
                            "page_stage": (
                                page.page_stage.value if page.page_stage else None
                            ),
                            "page_spec_version_id": page.page_spec_version_id,
                            "current_artifact_id": page.current_artifact_id,
                            "error": (
                                None
                                if page.error is None
                                else safe_provider_failure_error(page.error)
                            ),
                            "recovery_metadata": page.recovery_metadata,
                        },
                    )
                    for page in pages
                ),
                interrupts=tuple(
                    EntityView(
                        id=interrupt.id,
                        status=interrupt.status.value,
                        values={
                            "step_id": interrupt.step_id,
                            "page_id": interrupt.page_id,
                            "version": interrupt.version,
                            "form_schema": interrupt.form_schema,
                            "resume_payload": interrupt.resume_payload,
                            "resolved_at": interrupt.resolved_at,
                        },
                    )
                    for interrupt in interrupts
                ),
                snapshot_seq=run.next_event_seq - 1,
            )

    def read_after(
        self, run_id: str, after_seq: int, *, limit: int = 100
    ) -> ReplayBatch:
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if limit <= 0 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with session_scope(self._sessions) as session:
            self._begin_consistent_read(session)
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError(f"workflow run not found: {run_id}")
            latest_seq = run.next_event_seq - 1
            if after_seq > latest_seq:
                raise CursorExpired(
                    f"cursor {after_seq} is ahead of latest sequence {latest_seq}"
                )
            records = session.scalars(
                select(WorkflowEventRecord)
                .where(
                    WorkflowEventRecord.run_id == run_id,
                    WorkflowEventRecord.run_seq > after_seq,
                )
                .order_by(WorkflowEventRecord.run_seq)
                .limit(limit)
            ).all()
            if after_seq < latest_seq and (
                not records or records[0].run_seq != after_seq + 1
            ):
                return ReplayBatch((), after_seq, True)
            events = tuple(self._event(record) for record in records)
            next_cursor = events[-1].run_seq if events else after_seq
            return ReplayBatch(events, next_cursor, False)

    def prune_ephemeral(self, *, before: datetime) -> int:
        with session_scope(self._sessions) as session:
            result = session.execute(
                delete(WorkflowEventRecord).where(
                    WorkflowEventRecord.durability == EventDurability.EPHEMERAL,
                    WorkflowEventRecord.recorded_at < before,
                )
            )
            return int(result.rowcount or 0)

    def ensure_run(self, run_id: str) -> int:
        """Return the latest sequence or raise before an SSE response starts."""

        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError(f"workflow run not found: {run_id}")
            return run.next_event_seq - 1

    @staticmethod
    def _begin_consistent_read(session: Any) -> None:
        """Force sqlite3 legacy transaction mode to pin a WAL read snapshot."""

        session.connection().exec_driver_sql("BEGIN")

    @staticmethod
    def _event(record: WorkflowEventRecord) -> EventEnvelope:
        occurred_at = record.occurred_at
        recorded_at = record.recorded_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        return EventEnvelope(
            event_id=record.event_id,
            run_id=record.run_id,
            run_seq=record.run_seq,
            event_type=record.event_type,
            schema_version=record.schema_version,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload=record.payload,
            durability=record.durability,
            step_id=record.step_id,
            page_id=record.page_id,
            operation_id=record.operation_id,
            trace_id=record.trace_id,
        )


__all__ = [
    "CursorExpired",
    "EventDurability",
    "EventEnvelope",
    "EventPayloadRejected",
    "ReplayBatch",
    "RunStateSnapshot",
    "WorkflowEventStore",
]
