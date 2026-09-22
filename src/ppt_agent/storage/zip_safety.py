"""Validation for untrusted ZIP central-directory entries."""

from __future__ import annotations

import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from zipfile import ZipInfo


class UnsafeZipEntry(ValueError):
    """A ZIP member cannot be mapped to a safe relative destination."""


@dataclass(frozen=True, slots=True)
class SafeZipMember:
    """A ZIP entry with a normalized, Workspace-relative destination."""

    info: ZipInfo
    path: PurePosixPath
    is_directory: bool


def validate_zip_members(
    members: Iterable[ZipInfo],
) -> tuple[SafeZipMember, ...]:
    """Validate and normalize members before any extraction occurs."""

    validated: list[SafeZipMember] = []
    destinations: set[str] = set()
    for info in members:
        name = info.filename
        if "\0" in name:
            raise UnsafeZipEntry("ZIP entry name contains NUL")
        if _is_symlink(info):
            raise UnsafeZipEntry(f"ZIP entry is a symbolic link: {name!r}")

        windows = PureWindowsPath(name)
        posix = PurePosixPath(name)
        if not name or windows.drive or windows.root or posix.is_absolute():
            raise UnsafeZipEntry(f"ZIP entry is not relative: {name!r}")

        normalized = name.replace("\\", "/")
        is_directory = normalized.endswith("/")
        components = normalized.rstrip("/").split("/")
        if any(component in {"", ".", ".."} for component in components):
            raise UnsafeZipEntry(f"ZIP entry has an unsafe path component: {name!r}")

        path = PurePosixPath(*components)
        destination = path.as_posix()
        if destination in destinations:
            raise UnsafeZipEntry(f"ZIP contains duplicate destination: {destination!r}")
        destinations.add(destination)
        validated.append(
            SafeZipMember(info=info, path=path, is_directory=is_directory)
        )
    return tuple(validated)


def _is_symlink(info: ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_IFMT(unix_mode) == stat.S_IFLNK
