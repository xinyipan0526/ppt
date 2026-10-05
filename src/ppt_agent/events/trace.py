"""Safe, bounded SQLite projection for a single workflow Run trace."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ppt_agent.operations.provider import (
    safe_provider_failure_error,
    safe_provider_failure_summary,
)
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ProviderAttemptRecord,
    ProviderOperationRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)


MAX_ATTEMPTS_PER_OPERATION = 20


class RunTraceService:
    """Read only fields explicitly safe to return to the local browser."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def get(
        self,
        run_id: str,
        *,
        event_limit: int,
        operation_limit: int,
    ) -> dict[str, object]:
        """Return one transactionally consistent and bounded trace projection."""

        with session_scope(self._sessions) as session:
            session.connection().exec_driver_sql("BEGIN")
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError("run not found")
            events = list(
                session.scalars(
                    select(WorkflowEventRecord)
                    .where(WorkflowEventRecord.run_id == run_id)
                    .order_by(WorkflowEventRecord.run_seq)
                    .limit(event_limit)
                )
            )
            operations = list(
                session.scalars(
                    select(ProviderOperationRecord)
                    .where(ProviderOperationRecord.run_id == run_id)
                    .order_by(
                        ProviderOperationRecord.created_at,
                        ProviderOperationRecord.id,
                    )
                    .limit(operation_limit)
                )
            )
            attempts_by_operation: dict[str, list[ProviderAttemptRecord]] = {
                operation.id: [] for operation in operations
            }
            if attempts_by_operation:
                attempt_records = session.scalars(
                    select(ProviderAttemptRecord)
                    .where(
                        ProviderAttemptRecord.operation_id.in_(
                            attempts_by_operation
                        )
                    )
                    .order_by(
                        ProviderAttemptRecord.operation_id,
                        ProviderAttemptRecord.attempt_no,
                    )
                    .limit(operation_limit * MAX_ATTEMPTS_PER_OPERATION)
                )
                for attempt in attempt_records:
                    bucket = attempts_by_operation[attempt.operation_id]
                    if len(bucket) < MAX_ATTEMPTS_PER_OPERATION:
                        bucket.append(attempt)

            return {
                "run": _run(run),
                "events": [_event(event) for event in events],
                "operations": [
                    {
                        "operation": _operation(operation),
                        "attempts": [
                            _attempt(attempt)
                            for attempt in attempts_by_operation[operation.id]
                        ],
                    }
                    for operation in operations
                ],
            }


def _run(record: WorkflowRunRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "workflow_name": record.workflow_name,
        "workflow_version": record.workflow_version,
        "status": record.status.value,
        "error": None if record.error is None else safe_provider_failure_error(record.error),
    }


def _event(record: WorkflowEventRecord) -> dict[str, object]:
    return {
        "event_id": record.event_id,
        "run_seq": record.run_seq,
        "event_type": record.event_type,
        "schema_version": record.schema_version,
        "occurred_at": _datetime(record.occurred_at),
        "recorded_at": _datetime(record.recorded_at),
        "step_id": record.step_id,
        "page_id": record.page_id,
        "operation_id": record.operation_id,
        "trace_id": record.trace_id,
        "durability": record.durability.value,
    }


def _operation(record: ProviderOperationRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "operation_kind": record.operation_kind,
        "provider": record.provider,
        "model": record.model,
        "aggregate_status": record.aggregate_status.value,
        "selected_attempt_id": record.selected_attempt_id,
        "has_unknown_attempts": record.has_unknown_attempts,
        "step_id": record.step_id,
        "page_id": record.page_id,
    }


def _attempt(record: ProviderAttemptRecord) -> dict[str, object]:
    safe_error = safe_provider_failure_error({"code": record.error_code})
    return {
        "id": record.id,
        "attempt_no": record.attempt_no,
        "status": record.status.value,
        "request_started_at": _datetime(record.request_started_at),
        "response_received_at": _datetime(record.response_received_at),
        "error_code": safe_error["code"] if record.error_code is not None else None,
        "response_summary": safe_provider_failure_summary(record.response_summary),
    }


def _datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


__all__ = ["MAX_ATTEMPTS_PER_OPERATION", "RunTraceService"]
