"""Bounded background maintenance for durable runtime records."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ppt_agent.events.store import WorkflowEventStore


class EventRetentionTask:
    """Periodically prune only disposable event projections."""

    def __init__(
        self,
        store: WorkflowEventStore,
        *,
        retention: timedelta,
        interval: float,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("event retention must be positive")
        if interval <= 0:
            raise ValueError("maintenance interval must be positive")
        self._store = store
        self._retention = retention
        self._interval = interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="event-retention")

    async def stop(self, *, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("maintenance shutdown timeout must be non-negative")
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return True
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return False
        return True

    async def _run(self) -> None:
        while not self._stop.is_set():
            cutoff = datetime.now(UTC) - self._retention
            try:
                await asyncio.to_thread(self._store.prune_ephemeral, before=cutoff)
            except Exception:
                # Maintenance failure must not terminate workflow scheduling. A later
                # interval retries; persistent failures belong in observability.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
