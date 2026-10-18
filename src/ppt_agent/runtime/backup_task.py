"""Periodic online database backup owned by the application runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ppt_agent.runtime.maintenance import _MaintenanceFailureReporter
from ppt_agent.storage.maintenance import (
    create_online_backup,
    retain_recent_backups,
)


class BackupShutdownTimeout(TimeoutError):
    """The backup worker may still own runtime storage resources."""


BackupCreator = Callable[[Path, Path], tuple[Path, Path]]
BackupRetainer = Callable[..., tuple[Path, ...]]
MaintenanceLock = Callable[[], AbstractContextManager[None]]


class BackupTask:
    """Create and retain online backups without blocking application readiness."""

    def __init__(
        self,
        database_path: Path,
        backup_directory: Path,
        trash_directory: Path,
        *,
        maintenance_lock: MaintenanceLock,
        interval: float = 86_400.0,
        retention: int = 7,
        create_backup: BackupCreator = create_online_backup,
        retain_backups: BackupRetainer = retain_recent_backups,
    ) -> None:
        if interval <= 0:
            raise ValueError("backup interval must be positive")
        if retention < 1:
            raise ValueError("backup retention must be positive")
        self._database_path = database_path
        self._backup_directory = backup_directory
        self._trash_directory = trash_directory
        self._maintenance_lock = maintenance_lock
        self._interval = interval
        self._retention = retention
        self._create_backup = create_backup
        self._retain_backups = retain_backups
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure_reporter = _MaintenanceFailureReporter("automatic_backup")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="automatic-backup")

    async def stop(self, *, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("backup shutdown timeout must be non-negative")
        self._stop.set()
        task = self._task
        if task is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError as exc:
            raise BackupShutdownTimeout(
                "automatic backup did not stop within the shutdown timeout"
            ) from exc
        self._task = None
        return True

    async def tick(self) -> Path:
        return await asyncio.to_thread(self._backup_once)

    def _backup_once(self) -> Path:
        with self._maintenance_lock():
            database, _manifest = self._create_backup(
                self._database_path,
                self._backup_directory,
            )
            self._retain_backups(
                self._backup_directory,
                self._trash_directory,
                keep=self._retention,
            )
            return database

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            else:
                return
            try:
                await self.tick()
            except Exception as exc:
                self._failure_reporter.record_failure(
                    exc,
                    next_run_at=datetime.now(UTC)
                    + timedelta(seconds=self._interval),
                )
            else:
                self._failure_reporter.record_success()
