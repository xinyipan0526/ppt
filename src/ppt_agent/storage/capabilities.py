"""Startup probes for Workspace filesystem reliability guarantees."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

from ppt_agent.storage.database import DatabaseConfig, JournalMode
from ppt_agent.storage.locking import WorkspaceInstanceLock, WorkspaceLockUnavailable
from ppt_agent.storage.workspace import UnsafeWorkspacePath, WorkspacePaths


class FilesystemKind(str, Enum):
    LOCAL = "local"
    NETWORK = "network"
    SYNC = "sync"
    UNKNOWN = "unknown"


FilesystemClassifier: TypeAlias = Callable[[Path], FilesystemKind]


class WorkspaceCapabilityError(RuntimeError):
    """The active Workspace lacks a required durability guarantee."""


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilities:
    root: Path
    filesystem_kind: FilesystemKind
    atomic_replace: bool
    exclusive_lock: bool
    file_fsync: bool
    directory_fsync: bool | None
    same_device: bool
    free_bytes: int
    journal_mode: JournalMode
    warnings: tuple[str, ...]

    def database_config(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 5_000,
        wal_autocheckpoint: int = 1_000,
    ) -> DatabaseConfig:
        database_path = Path(path).resolve(strict=False)
        if database_path == self.root or not database_path.is_relative_to(self.root):
            raise WorkspaceCapabilityError(
                "State database path must be below the verified Workspace"
            )
        return DatabaseConfig(
            path=database_path,
            journal_mode=self.journal_mode,
            busy_timeout_ms=busy_timeout_ms,
            wal_autocheckpoint=wal_autocheckpoint,
        )


def probe_workspace(
    root: Path,
    *,
    production: bool = True,
    minimum_free_bytes: int = 1_073_741_824,
    classifier: FilesystemClassifier | None = None,
) -> WorkspaceCapabilities:
    """Probe required storage semantics and select an effective journal mode."""

    if minimum_free_bytes < 0:
        raise ValueError("minimum_free_bytes must be non-negative")
    classify = classifier or classify_filesystem
    requested_root = Path(root).expanduser().absolute()
    paths = WorkspacePaths(requested_root, create=True)
    filesystem_kind = classify(paths.root)
    if filesystem_kind is FilesystemKind.NETWORK:
        raise WorkspaceCapabilityError(
            "Known network filesystems cannot host the active Workspace"
        )
    if filesystem_kind is FilesystemKind.UNKNOWN and production:
        raise WorkspaceCapabilityError(
            "Cannot classify Workspace filesystem in production mode"
        )

    free_bytes = shutil.disk_usage(paths.root).free
    if free_bytes < minimum_free_bytes:
        raise WorkspaceCapabilityError(
            f"Workspace has insufficient free space: {free_bytes} bytes"
        )

    warnings: list[str] = []
    if filesystem_kind is FilesystemKind.SYNC:
        warnings.append(
            "Workspace is inside a synchronized directory; local storage is recommended"
        )
    elif filesystem_kind is FilesystemKind.UNKNOWN:
        warnings.append("Workspace filesystem is unverified outside production mode")

    probe_path = paths.resolve(f".capability-probe-{uuid4()}")
    probe_path.mkdir()
    try:
        atomic_replace = _probe_atomic_replace(probe_path)
        if not atomic_replace:
            raise WorkspaceCapabilityError("Workspace lacks reliable atomic replace")

        exclusive_lock = _probe_exclusive_lock(probe_path)
        if not exclusive_lock:
            raise WorkspaceCapabilityError("Workspace lacks a reliable exclusive lock")

        file_fsync = _probe_file_fsync(probe_path)
        if not file_fsync:
            raise WorkspaceCapabilityError("Workspace lacks reliable file fsync")

        directory_fsync = _probe_directory_fsync(probe_path)
        if directory_fsync is None:
            warnings.append("Directory fsync cannot be verified on this platform")
        elif not directory_fsync:
            raise WorkspaceCapabilityError("Workspace directory fsync failed")

        same_device = os.stat(paths.root).st_dev == os.stat(probe_path).st_dev
        if not same_device:
            raise WorkspaceCapabilityError(
                "Workspace staging and final paths are not on the same device"
            )

        journal_mode = _probe_wal(probe_path)
        if journal_mode != "WAL":
            if directory_fsync is not True:
                raise WorkspaceCapabilityError(
                    "Rollback journal fallback requires verified directory fsync"
                )
            journal_mode = "DELETE"
            warnings.append(
                "SQLite WAL is unavailable; using rollback journal mode"
            )

        return WorkspaceCapabilities(
            root=paths.root,
            filesystem_kind=filesystem_kind,
            atomic_replace=atomic_replace,
            exclusive_lock=exclusive_lock,
            file_fsync=file_fsync,
            directory_fsync=directory_fsync,
            same_device=same_device,
            free_bytes=free_bytes,
            journal_mode=journal_mode,
            warnings=tuple(warnings),
        )
    finally:
        _remove_probe_directory(paths, probe_path)


def classify_filesystem(path: Path) -> FilesystemKind:
    """Classify known network and synchronized filesystem locations."""

    if os.name == "nt":
        platform_kind = _classify_windows_filesystem(path)
    else:
        platform_kind = _classify_posix_filesystem(path)
    if platform_kind is FilesystemKind.NETWORK:
        return platform_kind

    lowered_parts = {part.casefold() for part in path.parts}
    sync_names = {"onedrive", "dropbox", "icloud drive", "icloudrive"}
    if lowered_parts & sync_names:
        return FilesystemKind.SYNC
    return platform_kind


def _classify_windows_filesystem(path: Path) -> FilesystemKind:
    value = str(path)
    if value.startswith(("\\\\", "//")):
        return FilesystemKind.NETWORK
    drive = path.drive
    if not drive:
        return FilesystemKind.UNKNOWN
    try:
        import ctypes

        drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
    except (AttributeError, OSError):
        return FilesystemKind.UNKNOWN
    if drive_type == 4:
        return FilesystemKind.NETWORK
    if drive_type in {2, 3, 5, 6}:
        return FilesystemKind.LOCAL
    return FilesystemKind.UNKNOWN


def _classify_posix_filesystem(path: Path) -> FilesystemKind:
    mounts = Path("/proc/mounts")
    if not mounts.exists():
        return FilesystemKind.UNKNOWN
    network_types = {
        "9p",
        "afs",
        "cifs",
        "fuse.sshfs",
        "nfs",
        "nfs4",
        "smb3",
        "smbfs",
    }
    candidates: list[tuple[int, str]] = []
    try:
        for line in mounts.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            mount_point = Path(fields[1].replace("\\040", " ")).resolve()
            if path == mount_point or path.is_relative_to(mount_point):
                candidates.append((len(mount_point.parts), fields[2]))
    except OSError:
        return FilesystemKind.UNKNOWN
    if not candidates:
        return FilesystemKind.UNKNOWN
    filesystem_type = max(candidates)[1]
    if filesystem_type in network_types:
        return FilesystemKind.NETWORK
    return FilesystemKind.LOCAL


def _probe_atomic_replace(directory: Path) -> bool:
    source = directory / "replace-source"
    destination = directory / "replace-destination"
    try:
        source.write_bytes(b"source")
        destination.write_bytes(b"old")
        os.replace(source, destination)
        return not source.exists() and destination.read_bytes() == b"source"
    except OSError:
        return False


def _probe_exclusive_lock(directory: Path) -> bool:
    paths = WorkspacePaths(directory)
    first = WorkspaceInstanceLock(paths, ".lock-probe")
    second = WorkspaceInstanceLock(paths, ".lock-probe")
    try:
        first.acquire()
        try:
            second.acquire()
        except WorkspaceLockUnavailable:
            return True
        else:
            second.release()
            return False
    except OSError:
        return False
    finally:
        try:
            first.release()
        except RuntimeError:
            pass


def _probe_file_fsync(directory: Path) -> bool:
    path = directory / "fsync-probe"
    try:
        with path.open("wb") as output:
            output.write(b"durable")
            output.flush()
            os.fsync(output.fileno())
        return True
    except OSError:
        return False


def _probe_directory_fsync(directory: Path) -> bool | None:
    if os.name == "nt":
        return None
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _probe_wal(directory: Path) -> JournalMode:
    database = directory / "wal-probe.db"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        actual = str(row[0]).upper() if row else ""
    finally:
        connection.close()
    return "WAL" if actual == "WAL" else "DELETE"


def _remove_probe_directory(paths: WorkspacePaths, probe_path: Path) -> None:
    try:
        verified = paths.verify(probe_path)
    except (FileNotFoundError, UnsafeWorkspacePath):
        return
    shutil.rmtree(verified)
