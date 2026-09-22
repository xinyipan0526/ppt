"""Shared event append primitive used inside workflow transactions."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from ppt_agent.domain.events import EventDurability
from ppt_agent.events.policy import (
    ABSOLUTE_MAX_PAYLOAD_BYTES,
    validate_event_payload,
    validate_event_type,
)
from ppt_agent.storage.models import WorkflowEventRecord, WorkflowRunRecord


def append_event_record(
    session: Session,
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    schema_version: int = 1,
    occurred_at: datetime | None = None,
    step_id: str | None = None,
    page_id: str | None = None,
    operation_id: str | None = None,
    trace_id: str | None = None,
    durability: EventDurability = EventDurability.CORE,
    max_payload_bytes: int = ABSOLUTE_MAX_PAYLOAD_BYTES,
) -> WorkflowEventRecord:
    """Allocate a sequence atomically and stage an event in ``session``."""

    validate_event_type(event_type)
    validate_event_payload(payload, max_payload_bytes=max_payload_bytes)
    allocated = session.scalar(
        update(WorkflowRunRecord)
        .where(WorkflowRunRecord.id == run_id)
        .values(next_event_seq=WorkflowRunRecord.next_event_seq + 1)
        .returning(WorkflowRunRecord.next_event_seq - 1)
    )
    if allocated is None:
        raise LookupError(f"workflow run not found: {run_id}")

    record = WorkflowEventRecord(
        event_id=str(uuid4()),
        run_id=run_id,
        run_seq=int(allocated),
        event_type=event_type,
        schema_version=schema_version,
        occurred_at=occurred_at or datetime.now(UTC),
        step_id=step_id,
        page_id=page_id,
        operation_id=operation_id,
        trace_id=trace_id,
        durability=durability,
        payload=payload,
    )
    session.add(record)
    return record
