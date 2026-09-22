"""Cross-platform, process-scoped lock for one active Workspace."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from ppt_agent.storage.workspace import WorkspacePaths


class WorkspaceLockUnavailable(RuntimeError):
    """Another process currently owns the Workspace lock."""


class WorkspaceInstanceLock:
    """Hold a non-blocking OS file lock for an application lifetime."""

    def __init__(
        self,
        paths: WorkspacePaths,
        name: str = ".ppt-agent.lock",
    ) -> None:
        self._paths = paths
        self._path = paths.resolve(name)
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            raise RuntimeError("Workspace lock is already acquired")

        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                os.fsync(lock_file.fileno())
            lock_file.seek(0)
            if not _try_lock(lock_file):
                raise WorkspaceLockUnavailable(
                    f"Workspace is already locked: {self._paths.root}"
                )

            metadata = json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
            ).encode("utf-8")
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(metadata)
            lock_file.flush()
            os.fsync(lock_file.fileno())
            self._paths.verify(self._path)
        except BaseException:
            if _is_locked(lock_file):
                _unlock(lock_file)
            lock_file.close()
            raise
        self._file = lock_file

    def release(self) -> None:
        if self._file is None:
            raise RuntimeError("Workspace lock is not acquired")
        lock_file = self._file
        self._file = None
        try:
            _unlock(lock_file)
        finally:
            lock_file.close()

    def __enter__(self) -> WorkspaceInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _try_lock(lock_file: BinaryIO) -> bool:
    lock_file.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    setattr(lock_file, "_ppt_agent_locked", True)
    return True


def _unlock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    setattr(lock_file, "_ppt_agent_locked", False)


def _is_locked(lock_file: BinaryIO) -> bool:
    return bool(getattr(lock_file, "_ppt_agent_locked", False))
