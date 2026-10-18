"""In-process task ownership with durable admission and shutdown boundaries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from ppt_agent.domain.state_machine import RunEvent, RunStatus


logger = logging.getLogger(__name__)
from ppt_agent.runtime.repository import WorkflowRepository


RunExecutor = Callable[[str], Awaitable[None]]


class SupervisorClosed(RuntimeError):
    pass


class TaskSupervisor:
    def __init__(
        self,
        repository: WorkflowRepository,
        executor: RunExecutor,
        *,
        global_limit: int = 4,
        project_limit: int = 2,
        provider_limits: dict[str, int] | None = None,
        cancellation_executor: RunExecutor | None = None,
    ) -> None:
        if global_limit < 1 or project_limit < 1:
            raise ValueError("supervisor concurrency limits must be positive")
        self._repository = repository
        self._executor = executor
        self._cancellation_executor = cancellation_executor
        self._global = asyncio.Semaphore(global_limit)
        self._project_limit = project_limit
        self._projects: dict[str, asyncio.Semaphore] = {}
        self._providers = {
            name: asyncio.Semaphore(limit)
            for name, limit in (provider_limits or {}).items()
            if limit > 0
        }
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    def schedule(self, run_id: str) -> bool:
        if self._closed:
            raise SupervisorClosed("TaskSupervisor is not accepting work")
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return False
        task = asyncio.create_task(self._run(run_id), name=f"workflow-run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed, rid=run_id: self._finished(rid, completed))
        return True

    async def cancel(self, run_id: str) -> bool:
        snapshot, accepted = await asyncio.to_thread(
            self._repository.request_cancellation, run_id
        )
        if snapshot.status is not RunStatus.CANCEL_REQUESTED:
            return False
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._cancellation_executor is not None:
            await self._cancellation_executor(run_id)
        return accepted

    @asynccontextmanager
    async def provider_slot(self, provider: str) -> AsyncIterator[None]:
        semaphore = self._providers.get(provider)
        if semaphore is None:
            yield
            return
        async with semaphore:
            yield

    async def shutdown(self, *, timeout: float) -> tuple[str, ...]:
        if timeout < 0:
            raise ValueError("shutdown timeout must be non-negative")
        self._closed = True
        tasks = dict(self._tasks)
        if not tasks:
            return ()
        done, pending = await asyncio.wait(tasks.values(), timeout=timeout)
        interrupted = [run_id for run_id, task in tasks.items() if task in pending]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for run_id in interrupted:
            await asyncio.to_thread(
                self._repository.mark_run_recovery_required,
                run_id,
                reason="graceful_shutdown_timeout",
            )
        return tuple(sorted(interrupted))

    async def _run(self, run_id: str) -> None:
        snapshot = await asyncio.to_thread(self._repository.get_run, run_id)
        project = self._projects.setdefault(
            snapshot.project_id, asyncio.Semaphore(self._project_limit)
        )
        try:
            async with self._global, project:
                current = await asyncio.to_thread(self._repository.get_run, run_id)
                if current.status is RunStatus.QUEUED:
                    await asyncio.to_thread(
                        self._repository.transition_run, run_id, RunEvent.DISPATCHED
                    )
                elif current.status is RunStatus.CANCEL_REQUESTED:
                    if self._cancellation_executor is not None:
                        await self._cancellation_executor(run_id)
                    return
                else:
                    return
                await self._executor(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("workflow executor failed", extra={"run_id": run_id})
            await asyncio.to_thread(
                self._repository.mark_run_recovery_required,
                run_id,
                reason=f"executor_failed:{type(exc).__name__}",
            )

    def _finished(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        if not task.cancelled():
            task.exception()
