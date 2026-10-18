"""Process-local projection of durable export jobs."""

from __future__ import annotations

import asyncio
import logging
from threading import Event
from typing import Protocol

from ppt_agent.workflows.exporting import ExportInterrupted


logger = logging.getLogger(__name__)
_RESCHEDULE_RECHECK_SECONDS = 1.0


class ExportJobs(Protocol):
    def recoverable_ids(self) -> tuple[str, ...]: ...
    def status(self, export_id: str): ...
    def export(self, export_id: str, **kwargs) -> str: ...
    def fail(
        self,
        export_id: str,
        *,
        code: str,
        expected_attempt_count: int | None = None,
    ) -> None: ...


class ExportSupervisorClosed(RuntimeError):
    pass


class ExportShutdownTimeout(RuntimeError):
    """A worker thread outlived the bounded cooperative shutdown window."""

    def __init__(self, export_ids: tuple[str, ...]) -> None:
        self.export_ids = export_ids
        super().__init__(
            "export worker threads still running: " + ", ".join(export_ids)
        )


class ExportSupervisor:
    def __init__(self, exports: ExportJobs, *, concurrency: int = 2) -> None:
        if concurrency < 1:
            raise ValueError("export concurrency must be positive")
        # Alembic's ``fileConfig`` may disable loggers imported before the
        # runtime migration phase.  A newly composed supervisor must restore
        # its audit/error channel before accepting work.
        logger.disabled = False
        self._exports = exports
        self._limit = asyncio.Semaphore(concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._interrupts: dict[str, Event] = {}
        self._reschedule_requested: set[str] = set()
        self._reschedule_handles: dict[str, asyncio.TimerHandle] = {}
        self._closed = False

    @property
    def active_export_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    def schedule(self, export_id: str) -> bool:
        if self._closed:
            raise ExportSupervisorClosed("ExportSupervisor is not accepting work")
        existing = self._tasks.get(export_id)
        if existing is not None and not existing.done():
            # An explicit retry can atomically requeue the durable row while
            # its previous process-local owner is still unwinding. Remember
            # that collision so ownership is reconsidered after the old task
            # has actually released it.
            self._reschedule_requested.add(export_id)
            return False
        handle = self._reschedule_handles.pop(export_id, None)
        if handle is not None:
            handle.cancel()
        self._reschedule_requested.discard(export_id)
        interrupted = Event()
        task = asyncio.create_task(
            self._run(export_id, interrupted), name=f"export:{export_id}"
        )
        self._interrupts[export_id] = interrupted
        self._tasks[export_id] = task
        task.add_done_callback(
            lambda completed, eid=export_id: self._finished(eid, completed)
        )
        return True

    def recover(self) -> tuple[str, ...]:
        scheduled = []
        for export_id in self._exports.recoverable_ids():
            if self.schedule(export_id):
                scheduled.append(export_id)
        return tuple(scheduled)

    async def shutdown(self, *, timeout: float) -> tuple[str, ...]:
        if timeout < 0:
            raise ValueError("shutdown timeout must be non-negative")
        self._closed = True
        for handle in self._reschedule_handles.values():
            handle.cancel()
        self._reschedule_handles.clear()
        self._reschedule_requested.clear()
        tasks = dict(self._tasks)
        if not tasks:
            return ()
        # ``asyncio.to_thread`` cancellation does not stop its worker thread.
        # Signal first, then keep the asyncio tasks alive until their workers
        # have crossed a cooperative boundary.  Callers must retain database,
        # Artifact and workspace-lock ownership if this bounded wait expires.
        for export_id in tasks:
            self._interrupts[export_id].set()
        _, pending = await asyncio.wait(tasks.values(), timeout=timeout)
        if pending:
            raise ExportShutdownTimeout(tuple(sorted(
                export_id for export_id, task in tasks.items() if task in pending
            )))
        return tuple(sorted(tasks))

    async def _run(self, export_id: str, interrupted: Event) -> None:
        expected_attempt_count: int | None = None
        try:
            async with self._limit:
                snapshot = await asyncio.to_thread(self._exports.status, export_id)
                expected_attempt_count = snapshot.attempt_count + 1
                await asyncio.to_thread(
                    self._exports.export,
                    export_id,
                    interrupted=interrupted.is_set,
                )
        except asyncio.CancelledError:
            raise
        except ExportInterrupted:
            return
        except Exception as exc:
            logger.error(
                "export executor failed",
                extra={
                    "export_id": export_id,
                    "exception_type": type(exc).__name__,
                },
            )
            try:
                await asyncio.to_thread(
                    self._exports.fail,
                    export_id,
                    code="export_failed",
                    expected_attempt_count=expected_attempt_count,
                )
            except Exception as persistence_exc:
                logger.error(
                    "export failure persistence failed",
                    extra={
                        "export_id": export_id,
                        "exception_type": type(persistence_exc).__name__,
                    },
                )

    def _finished(self, export_id: str, task: asyncio.Task[None]) -> None:
        owned = self._tasks.get(export_id) is task
        if owned:
            self._tasks.pop(export_id, None)
            self._interrupts.pop(export_id, None)
        reschedule = export_id in self._reschedule_requested
        if not task.cancelled():
            task.exception()
        if self._closed:
            self._reschedule_requested.discard(export_id)
            return
        if not owned or not reschedule:
            return
        self._check_reschedule(export_id)

    def _check_reschedule(self, export_id: str) -> None:
        if self._closed:
            self._reschedule_requested.discard(export_id)
            return
        if export_id in self._tasks:
            return
        try:
            recoverable = export_id in self._exports.recoverable_ids()
        except Exception as exc:
            logger.error(
                "export reschedule check failed",
                extra={
                    "export_id": export_id,
                    "exception_type": type(exc).__name__,
                },
            )
            self._defer_reschedule_check(export_id)
            return
        self._reschedule_requested.discard(export_id)
        if recoverable:
            self.schedule(export_id)

    def _defer_reschedule_check(self, export_id: str) -> None:
        if self._closed or export_id in self._reschedule_handles:
            return
        loop = asyncio.get_running_loop()
        self._reschedule_handles[export_id] = loop.call_later(
            _RESCHEDULE_RECHECK_SECONDS, self._retry_reschedule_check, export_id
        )

    def _retry_reschedule_check(self, export_id: str) -> None:
        self._reschedule_handles.pop(export_id, None)
        if self._closed:
            self._reschedule_requested.discard(export_id)
            return
        self._check_reschedule(export_id)
