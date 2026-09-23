"""Composition root for one durable, single-process Workspace runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import Engine

from ppt_agent.artifacts.store import ArtifactStore, ReconciliationReport
from ppt_agent.events.store import WorkflowEventStore
from ppt_agent.runtime.maintenance import EventRetentionTask
from ppt_agent.runtime.recovery import RecoveryCoordinator, RecoveryPlan
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.runtime.supervisor import RunExecutor, TaskSupervisor
from ppt_agent.storage.capabilities import (
    FilesystemClassifier,
    WorkspaceCapabilities,
    probe_workspace,
)
from ppt_agent.storage.database import create_session_factory, create_sqlite_engine
from ppt_agent.storage.locking import WorkspaceInstanceLock
from ppt_agent.storage.maintenance import run_migrations, verify_database_integrity
from ppt_agent.storage.workspace import WorkspacePaths


@dataclass(frozen=True, slots=True)
class ApplicationRuntimeConfig:
    workspace: Path
    database_name: str = "state.db"
    production: bool = True
    minimum_free_bytes: int = 1_073_741_824
    shutdown_timeout: float = 10.0
    event_retention: timedelta = timedelta(days=7)
    maintenance_interval: float = 3_600.0
    maintenance_shutdown_timeout: float = 5.0
    global_limit: int = 4
    project_limit: int = 2

    def __post_init__(self) -> None:
        if Path(self.database_name).name != self.database_name:
            raise ValueError("database_name must be a simple filename")
        if self.shutdown_timeout < 0:
            raise ValueError("shutdown_timeout must be non-negative")
        if self.maintenance_shutdown_timeout < 0:
            raise ValueError("maintenance_shutdown_timeout must be non-negative")


class ApplicationRuntime:
    """Own all process-lifetime resources and expose readiness explicitly."""

    def __init__(
        self,
        config: ApplicationRuntimeConfig,
        executor: RunExecutor,
        *,
        filesystem_classifier: FilesystemClassifier | None = None,
        migration_runner: Callable[[Path], None] = run_migrations,
        cancellation_executor: RunExecutor | None = None,
    ) -> None:
        self.config = config
        self._executor = executor
        self._classifier = filesystem_classifier
        self._migration_runner = migration_runner
        self._cancellation_executor = cancellation_executor
        self.capabilities: WorkspaceCapabilities | None = None
        self.engine: Engine | None = None
        self.repository: WorkflowRepository | None = None
        self.event_store: WorkflowEventStore | None = None
        self.artifacts: ArtifactStore | None = None
        self.supervisor: TaskSupervisor | None = None
        self.recovery_plan: RecoveryPlan | None = None
        self.reconciliation_report: ReconciliationReport | None = None
        self._lock: WorkspaceInstanceLock | None = None
        self._retention: EventRetentionTask | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        if self._ready or self._lock is not None:
            raise RuntimeError("application runtime is already started")
        try:
            self.capabilities = await asyncio.to_thread(
                probe_workspace,
                self.config.workspace,
                production=self.config.production,
                minimum_free_bytes=self.config.minimum_free_bytes,
                classifier=self._classifier,
            )
            paths = WorkspacePaths(self.capabilities.root)
            workspace_lock = WorkspaceInstanceLock(paths)
            await asyncio.to_thread(workspace_lock.acquire)
            self._lock = workspace_lock

            database_path = paths.resolve(self.config.database_name)
            await asyncio.to_thread(self._migration_runner, database_path)
            self.engine = create_sqlite_engine(
                self.capabilities.database_config(database_path)
            )
            await asyncio.to_thread(verify_database_integrity, self.engine)
            sessions = create_session_factory(self.engine)

            self.artifacts = ArtifactStore(sessions, paths.root)
            self.reconciliation_report = await asyncio.to_thread(
                self.artifacts.reconcile
            )
            self.repository = WorkflowRepository(sessions)
            self.event_store = WorkflowEventStore(sessions)
            self.recovery_plan = await asyncio.to_thread(
                RecoveryCoordinator(sessions).recover
            )
            if (
                self.recovery_plan.cancelling_run_ids
                and self._cancellation_executor is None
            ):
                raise RuntimeError(
                    "startup recovery requires a cancellation executor for "
                    "cancel_requested runs"
                )
            self.supervisor = TaskSupervisor(
                self.repository,
                self._executor,
                global_limit=self.config.global_limit,
                project_limit=self.config.project_limit,
                cancellation_executor=self._cancellation_executor,
            )
            for run_id in (
                *self.recovery_plan.queued_run_ids,
                *self.recovery_plan.cancelling_run_ids,
            ):
                self.supervisor.schedule(run_id)

            self._retention = EventRetentionTask(
                self.event_store,
                retention=self.config.event_retention,
                interval=self.config.maintenance_interval,
            )
            self._retention.start()
            self._ready = True
        except BaseException:
            try:
                await self._cleanup_failed_start()
            except BaseException:
                # Preserve the startup failure; every cleanup stage is already
                # attempted independently by stop().
                pass
            raise

    async def stop(self) -> tuple[str, ...]:
        self._ready = False
        interrupted: tuple[str, ...] = ()
        failure: BaseException | None = None
        if self._retention is not None:
            try:
                await self._retention.stop(
                    timeout=self.config.maintenance_shutdown_timeout
                )
            except BaseException as exc:
                failure = exc
            finally:
                self._retention = None
        if self.supervisor is not None:
            try:
                interrupted = await self.supervisor.shutdown(
                    timeout=self.config.shutdown_timeout
                )
            except BaseException as exc:
                failure = failure or exc
            finally:
                self.supervisor = None
        if self.engine is not None:
            try:
                await asyncio.to_thread(self.engine.dispose)
            except BaseException as exc:
                failure = failure or exc
            finally:
                self.engine = None
        if self._lock is not None:
            try:
                await asyncio.to_thread(self._lock.release)
            except BaseException as exc:
                failure = failure or exc
            finally:
                self._lock = None
        if failure is not None:
            raise failure
        return interrupted

    async def _cleanup_failed_start(self) -> None:
        try:
            await self.stop()
        finally:
            self.repository = None
            self.event_store = None
            self.artifacts = None
