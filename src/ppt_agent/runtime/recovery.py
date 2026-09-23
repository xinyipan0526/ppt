"""Deterministic startup normalization of durable workflow state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from ppt_agent.domain.state_machine import (
    AttemptEvent,
    AttemptStatus,
    PageEvent,
    PageStatus,
    RunEvent,
    RunStatus,
    StepEvent,
    StepStatus,
    transition_attempt,
    transition_page,
    transition_run,
    transition_step,
)
from ppt_agent.events.persistence import append_event_record
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    PageRecord,
    ProviderAttemptRecord,
    ProviderOperationRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    queued_run_ids: tuple[str, ...]
    cancelling_run_ids: tuple[str, ...]
    blocked_run_ids: tuple[str, ...]
    waiting_run_ids: tuple[str, ...]
    reconciling_attempt_ids: tuple[str, ...]


class RecoveryCoordinator:
    """Normalize crash-interrupted rows before in-memory scheduling begins."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def recover(self, *, now: datetime | None = None) -> RecoveryPlan:
        current_time = now or datetime.now(UTC)
        with session_scope(self._sessions) as session:
            operations = {
                record.id: record
                for record in session.scalars(select(ProviderOperationRecord))
            }
            reconciling: list[str] = []
            attempts = list(session.scalars(select(ProviderAttemptRecord)))
            for attempt in attempts:
                if attempt.status in {
                    AttemptStatus.SUBMITTED,
                    AttemptStatus.PROVIDER_RUNNING,
                }:
                    attempt.status = transition_attempt(
                        attempt.status, AttemptEvent.RECOVERY_STARTED
                    )
                    reconciling.append(attempt.id)
                    operation = operations[attempt.operation_id]
                    if operation.run_id is not None:
                        append_event_record(
                            session,
                            run_id=operation.run_id,
                            event_type="provider.reconciling",
                            operation_id=operation.id,
                            payload={
                                "operation_id": operation.id,
                                "attempt_id": attempt.id,
                                "status": attempt.status.value,
                            },
                        )

            uncertain_runs = {
                operation.run_id
                for attempt in attempts
                if attempt.status
                in {
                    AttemptStatus.RECONCILING,
                    AttemptStatus.CANCEL_REQUESTED,
                    AttemptStatus.OUTCOME_UNKNOWN,
                }
                for operation in (operations[attempt.operation_id],)
                if operation.run_id is not None
            }

            runs = list(
                session.scalars(select(WorkflowRunRecord).order_by(WorkflowRunRecord.id))
            )
            steps_by_run: dict[str, list[WorkflowStepRecord]] = {}
            for step in session.scalars(select(WorkflowStepRecord)):
                steps_by_run.setdefault(step.run_id, []).append(step)
            pages_by_run: dict[str, list[PageRecord]] = {}
            for page in session.scalars(select(PageRecord)):
                pages_by_run.setdefault(page.run_id, []).append(page)

            queued: list[str] = []
            cancelling: list[str] = []
            blocked: list[str] = []
            waiting: list[str] = []
            for run in runs:
                self._normalize_interrupted_entities(
                    session,
                    run,
                    steps_by_run.get(run.id, []),
                    pages_by_run.get(run.id, []),
                    current_time,
                )
                if run.status is RunStatus.CANCEL_REQUESTED:
                    cancelling.append(run.id)
                    continue
                if run.status in {RunStatus.WAITING_FOR_USER, RunStatus.WAITING_FOR_RETRY}:
                    waiting.append(run.id)
                    continue
                if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
                    continue
                if run.id in uncertain_runs:
                    blocked.append(run.id)
                    continue
                if run.status is RunStatus.RECOVERY_REQUIRED:
                    blocked.append(run.id)
                    continue
                if run.status is RunStatus.QUEUED:
                    queued.append(run.id)

            return RecoveryPlan(
                queued_run_ids=tuple(queued),
                cancelling_run_ids=tuple(cancelling),
                blocked_run_ids=tuple(blocked),
                waiting_run_ids=tuple(waiting),
                reconciling_attempt_ids=tuple(sorted(reconciling)),
            )

    @staticmethod
    def _normalize_interrupted_entities(
        session: object,
        run: WorkflowRunRecord,
        steps: list[WorkflowStepRecord],
        pages: list[PageRecord],
        now: datetime,
    ) -> None:
        if run.status is RunStatus.RUNNING:
            run.status = transition_run(run.status, RunEvent.PROCESS_RESTARTED)
            run.recovery_metadata = {"reason": "process_restarted"}
            append_event_record(
                session,  # type: ignore[arg-type]
                run_id=run.id,
                event_type="run.process_restarted",
                payload={"status": run.status.value},
            )
        elif (
            run.status is RunStatus.WAITING_FOR_RETRY
            and run.retry_at is not None
            and _as_utc(run.retry_at) <= now
        ):
            run.status = transition_run(run.status, RunEvent.RETRY_DUE)
            run.retry_at = None
            append_event_record(
                session,  # type: ignore[arg-type]
                run_id=run.id,
                event_type="run.retry_due",
                payload={"status": run.status.value},
            )
        for step in steps:
            if step.status is StepStatus.RUNNING:
                step.status = transition_step(step.status, StepEvent.PROCESS_RESTARTED)
                step.recovery_metadata = {"reason": "process_restarted"}
                append_event_record(
                    session,  # type: ignore[arg-type]
                    run_id=run.id,
                    step_id=step.id,
                    event_type="step.process_restarted",
                    payload={"step_id": step.id, "status": step.status.value},
                )
            elif (
                step.status is StepStatus.WAITING_FOR_RETRY
                and step.retry_at is not None
                and _as_utc(step.retry_at) <= now
            ):
                step.status = transition_step(step.status, StepEvent.RETRY_DUE)
                step.retry_at = None
                append_event_record(
                    session,  # type: ignore[arg-type]
                    run_id=run.id,
                    step_id=step.id,
                    event_type="step.retry_due",
                    payload={"step_id": step.id, "status": step.status.value},
                )
        for page in pages:
            if page.status is PageStatus.RUNNING:
                page.status = transition_page(page.status, PageEvent.PROCESS_RESTARTED)
                page.recovery_metadata = {"reason": "process_restarted"}
                append_event_record(
                    session,  # type: ignore[arg-type]
                    run_id=run.id,
                    page_id=page.id,
                    event_type="page.process_restarted",
                    payload={"page_id": page.id, "status": page.status.value},
                )

def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
