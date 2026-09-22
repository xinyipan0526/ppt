"""Canonical path boundary for files owned by one Workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeWorkspacePath(ValueError):
    """A path is not a safe member of the active Workspace."""


@dataclass(frozen=True, slots=True, init=False)
class WorkspacePaths:
    """Resolve and verify paths relative to a canonical Workspace root."""

    root: Path

    def __init__(self, root: Path, *, create: bool = False) -> None:
        candidate = Path(root).expanduser()
        if create:
            candidate.mkdir(parents=True, exist_ok=True)
        canonical = candidate.resolve(strict=True)
        if not canonical.is_dir():
            raise NotADirectoryError(canonical)
        object.__setattr__(self, "root", canonical)

    def resolve(
        self,
        relative: str | Path,
        *,
        must_exist: bool = False,
    ) -> Path:
        """Resolve a lexical relative path and prove it stays below the root."""

        parts = self._relative_parts(relative)
        resolved = self.root.joinpath(*parts).resolve(strict=must_exist)
        self._require_contained(resolved)
        return resolved

    def verify(self, path: Path, *, must_exist: bool = True) -> Path:
        """Re-resolve a filesystem path after I/O and re-check containment."""

        resolved = Path(path).resolve(strict=must_exist)
        self._require_contained(resolved)
        return resolved

    @staticmethod
    def _relative_parts(relative: str | Path) -> tuple[str, ...]:
        value = str(relative)
        if not value or "\0" in value:
            raise UnsafeWorkspacePath("Workspace path must be a non-empty relative path")

        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if windows.drive or windows.root or posix.is_absolute():
            raise UnsafeWorkspacePath("Workspace path must not be absolute or drive-qualified")

        normalized = value.replace("\\", "/")
        parts = tuple(normalized.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise UnsafeWorkspacePath("Workspace path contains an unsafe component")
        return parts

    def _require_contained(self, path: Path) -> None:
        if path == self.root or not path.is_relative_to(self.root):
            raise UnsafeWorkspacePath(f"Path escapes Workspace root: {path}")
