from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from threading import Event

import pytest

from ppt_agent.runtime.backup_task import BackupShutdownTimeout, BackupTask


def test_backup_tick_serializes_creation_and_retains_seven() -> None:
    async def scenario() -> None:
        calls: list[object] = []

        @contextmanager
        def maintenance_lock():
            calls.append("lock-enter")
            try:
                yield
            finally:
                calls.append("lock-exit")

        def create(database: Path, directory: Path):
            calls.append(("create", database, directory))
            return directory / "backup.db", directory / "backup.manifest.json"

        def retain(directory: Path, trash: Path, *, keep: int):
            calls.append(("retain", directory, trash, keep))
            return ()

        task = BackupTask(
            Path("state.db"),
            Path("backups"),
            Path("trash"),
            maintenance_lock=maintenance_lock,
            create_backup=create,
            retain_backups=retain,
        )

        result = await task.tick()

        assert result == Path("backups/backup.db")
        assert calls == [
            "lock-enter",
            ("create", Path("state.db"), Path("backups")),
            ("retain", Path("backups"), Path("trash"), 7),
            "lock-exit",
        ]

    asyncio.run(scenario())


def test_backup_failures_are_throttled_and_do_not_busy_loop(caplog) -> None:
    async def scenario() -> int:
        attempts = 0

        @contextmanager
        def maintenance_lock():
            yield

        def fail(_database: Path, _directory: Path):
            nonlocal attempts
            attempts += 1
            raise OSError("private-path-do-not-log")

        task = BackupTask(
            Path("state.db"),
            Path("backups"),
            Path("trash"),
            maintenance_lock=maintenance_lock,
            interval=0.02,
            create_backup=fail,
        )
        task.start()
        await asyncio.sleep(0.11)
        assert await task.stop(timeout=1) is True
        return attempts

    with caplog.at_level(logging.WARNING, logger="ppt_agent.runtime.maintenance"):
        attempts = asyncio.run(scenario())

    assert 3 <= attempts <= 6
    records = [
        record
        for record in caplog.records
        if getattr(record, "maintenance_task", None) == "automatic_backup"
    ]
    assert 2 <= len(records) < attempts
    assert all("private-path-do-not-log" not in record.getMessage() for record in records)
    assert all(
        isinstance(getattr(record, "next_run_at", None), str)
        and datetime.fromisoformat(record.next_run_at).tzinfo is UTC
        for record in records
    )


def test_backup_shutdown_is_bounded_without_releasing_owned_resources() -> None:
    async def scenario() -> None:
        entered = Event()
        release = Event()

        @contextmanager
        def maintenance_lock():
            yield

        def blocked(_database: Path, directory: Path):
            entered.set()
            assert release.wait(5)
            return directory / "backup.db", directory / "backup.manifest.json"

        task = BackupTask(
            Path("state.db"),
            Path("backups"),
            Path("trash"),
            maintenance_lock=maintenance_lock,
            interval=0.001,
            create_backup=blocked,
        )
        task.start()
        assert await asyncio.to_thread(entered.wait, 2)

        with pytest.raises(BackupShutdownTimeout):
            await task.stop(timeout=0.01)
        assert task.running is True

        release.set()
        assert await task.stop(timeout=2) is True
        assert task.running is False

    asyncio.run(scenario())
