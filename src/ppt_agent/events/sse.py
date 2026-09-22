"""Bounded database-backed SSE projection for workflow events."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from ppt_agent.events.store import CursorExpired, EventEnvelope, WorkflowEventStore


class EventStreamService:
    """Replay events in bounded batches without coupling clients to writers."""

    def __init__(
        self,
        store: WorkflowEventStore,
        *,
        poll_interval: float = 0.25,
        batch_size: int = 100,
        retry_ms: int = 3_000,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if batch_size <= 0 or batch_size > 1_000:
            raise ValueError("batch_size must be between 1 and 1000")
        if retry_ms <= 0:
            raise ValueError("retry_ms must be positive")
        self.store = store
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._retry_ms = retry_ms

    async def iter_sse(self, run_id: str, *, after_seq: int) -> AsyncIterator[str]:
        cursor = after_seq
        while True:
            try:
                batch = await asyncio.to_thread(
                    self.store.read_after,
                    run_id,
                    cursor,
                    limit=self._batch_size,
                )
            except CursorExpired:
                yield self._cursor_expired_frame(cursor)
                return
            except LookupError:
                yield self._run_unavailable_frame(run_id)
                return
            if batch.cursor_expired:
                yield self._cursor_expired_frame(cursor)
                return
            if batch.events:
                for event in batch.events:
                    yield self._event_frame(event)
                cursor = batch.next_cursor
                continue
            await asyncio.sleep(self._poll_interval)

    def snapshot(self, run_id: str):
        return self.store.get_run_snapshot(run_id)

    def ensure_run(self, run_id: str) -> int:
        return self.store.ensure_run(run_id)

    def _event_frame(self, event: EventEnvelope) -> str:
        data = {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "run_seq": event.run_seq,
            "schema_version": event.schema_version,
            "occurred_at": event.occurred_at.isoformat(),
            "recorded_at": event.recorded_at.isoformat(),
            "step_id": event.step_id,
            "page_id": event.page_id,
            "operation_id": event.operation_id,
            "trace_id": event.trace_id,
            "payload": event.payload,
        }
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return (
            f"id: {event.run_seq}\n"
            f"event: {event.event_type}\n"
            f"retry: {self._retry_ms}\n"
            f"data: {encoded}\n\n"
        )

    def _cursor_expired_frame(self, cursor: int) -> str:
        data = json.dumps(
            {"cursor": cursor, "refresh_required": True}, separators=(",", ":")
        )
        return f"event: cursor_expired\nretry: {self._retry_ms}\ndata: {data}\n\n"

    def _run_unavailable_frame(self, run_id: str) -> str:
        data = json.dumps(
            {"run_id": run_id, "refresh_required": True}, separators=(",", ":")
        )
        return f"event: run_unavailable\nretry: {self._retry_ms}\ndata: {data}\n\n"
