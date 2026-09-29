"""In-process scheduling for local material parsing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from ppt_agent.domain.materials import MaterialStatus
from ppt_agent.materials.store import MaterialReconciliationReport, MaterialStore


MaterialExecutor = Callable[[str], Awaitable[object]]


class MaterialLookup(Protocol):
    def get(self, material_id: str) -> object: ...


class MaterialSupervisorClosed(RuntimeError):
    pass


class MaterialSupervisor:
    def __init__(
        self,
        store: MaterialLookup,
        executor: MaterialExecutor,
        *,
        global_limit: int = 2,
        project_limit: int = 1,
    ) -> None:
        if global_limit < 1 or project_limit < 1:
            raise ValueError("material concurrency limits must be positive")
        self._store = store
        self._executor = executor
        self._global = asyncio.Semaphore(global_limit)
        self._project_limit = project_limit
        self._projects: dict[str, asyncio.Semaphore] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def active_material_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    def schedule(self, material_id: str) -> bool:
        if self._closed:
            raise MaterialSupervisorClosed("MaterialSupervisor is not accepting work")
        existing = self._tasks.get(material_id)
        if existing is not None and not existing.done():
            return False
        task = asyncio.create_task(self._run(material_id), name=f"material-parse:{material_id}")
        self._tasks[material_id] = task
        task.add_done_callback(
            lambda completed, mid=material_id: self._finished(mid, completed)
        )
        return True

    async def shutdown(self, *, timeout: float) -> tuple[str, ...]:
        if timeout < 0:
            raise ValueError("shutdown timeout must be non-negative")
        self._closed = True
        tasks = dict(self._tasks)
        if not tasks:
            return ()
        _done, pending = await asyncio.wait(tasks.values(), timeout=timeout)
        interrupted = sorted(mid for mid, task in tasks.items() if task in pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return tuple(interrupted)

    async def _run(self, material_id: str) -> None:
        snapshot = await asyncio.to_thread(self._store.get, material_id)
        project_id = str(getattr(snapshot, "project_id"))
        project = self._projects.setdefault(
            project_id, asyncio.Semaphore(self._project_limit)
        )
        # Take the narrow project slot first so a same-project waiter cannot
        # occupy a global slot and starve work from another project.
        async with project, self._global:
            await self._executor(material_id)

    def _finished(self, material_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(material_id) is task:
            self._tasks.pop(material_id, None)
        if not task.cancelled():
            task.exception()


class MaterialRuntime:
    """Startup reconciliation and scheduling owned by ApplicationRuntime."""

    def __init__(
        self,
        store: MaterialStore,
        supervisor: MaterialSupervisor,
        *,
        shutdown_timeout: float,
    ) -> None:
        self.store = store
        self.supervisor = supervisor
        self.shutdown_timeout = shutdown_timeout
        self.reconciliation_report: MaterialReconciliationReport | None = None

    async def start(self) -> None:
        self.reconciliation_report = await asyncio.to_thread(self.store.reconcile)
        for material in await asyncio.to_thread(self.store.list_pending):
            if material.status is MaterialStatus.UPLOADED:
                self.supervisor.schedule(material.id)

    async def stop(self, *, timeout: float | None = None) -> tuple[str, ...]:
        return await self.supervisor.shutdown(
            timeout=self.shutdown_timeout if timeout is None else timeout
        )
