"""Bounded background maintenance for durable runtime records."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

from ppt_agent.events.store import WorkflowEventStore
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.runtime.supervisor import TaskSupervisor


_LOGGER = logging.getLogger("ppt_agent.runtime.maintenance")
_MAX_CONSECUTIVE_FAILURES = 1024


class _MaintenanceFailureReporter:
    """Emit bounded, secret-free logs for a periodically failing task."""

    def __init__(self, task_name: str) -> None:
        self._task_name = task_name
        self._consecutive_failures = 0

    def record_failure(
        self,
        exc: Exception,
        *,
        next_run_at: datetime | None = None,
    ) -> None:
        self._consecutive_failures += 1
        # Alembic's fileConfig may disable loggers that already exist. A
        # maintenance failure must remain observable after any migration run.
        _LOGGER.disabled = False
        fields = {
            "maintenance_task": self._task_name,
            "failure_count": self._consecutive_failures,
            "exception_type": type(exc).__name__,
        }
        if next_run_at is not None:
            fields["next_run_at"] = next_run_at.astimezone(UTC).isoformat()
        if self._consecutive_failures == 1:
            _LOGGER.warning("maintenance task failed", extra=fields)
        elif (
            self._consecutive_failures <= _MAX_CONSECUTIVE_FAILURES
            and self._is_power_of_two(self._consecutive_failures)
        ):
            _LOGGER.warning("maintenance task failure summary", extra=fields)

    def record_success(self) -> None:
        self._consecutive_failures = 0

    @staticmethod
    def _is_power_of_two(value: int) -> bool:
        return value > 0 and value & (value - 1) == 0


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
        self._failure_reporter = _MaintenanceFailureReporter("event_retention")

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
            except Exception as exc:
                # A later interval retries; the reporter preserves an auditable,
                # bounded record without copying a provider payload or secret.
                self._failure_reporter.record_failure(
                    exc,
                    next_run_at=datetime.now(UTC) + timedelta(seconds=self._interval),
                )
            else:
                self._failure_reporter.record_success()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue


class ProviderRetryTask:
    """Poll persisted known-failure due times and enqueue safe retries."""

    def __init__(
        self,
        repository: WorkflowRepository,
        supervisor: TaskSupervisor,
        *,
        interval: float = 1.0,
    ) -> None:
        if interval <= 0:
            raise ValueError("retry poll interval must be positive")
        self._repository = repository
        self._supervisor = supervisor
        self._interval = interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure_reporter = _MaintenanceFailureReporter("provider_retry")

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="provider-retry-scheduler")

    async def stop(self, *, timeout: float) -> bool:
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

    async def tick(self, now: datetime | None = None) -> tuple[str, ...]:
        run_ids = await asyncio.to_thread(
            self._repository.release_due_provider_retries, now
        )
        for run_id in run_ids:
            self._supervisor.schedule(run_id)
        return run_ids

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                # Durable due times remain unconsumed or the run stays queued; a
                # later poll/startup scan can safely retry the local scheduling.
                self._failure_reporter.record_failure(
                    exc,
                    next_run_at=datetime.now(UTC) + timedelta(seconds=self._interval),
                )
            else:
                self._failure_reporter.record_success()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
