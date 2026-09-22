"""Persistence and workspace storage primitives."""

from ppt_agent.storage.capabilities import (
    FilesystemKind,
    WorkspaceCapabilities,
    WorkspaceCapabilityError,
    classify_filesystem,
    probe_workspace,
)
from ppt_agent.storage.locking import WorkspaceInstanceLock, WorkspaceLockUnavailable
from ppt_agent.storage.workspace import UnsafeWorkspacePath, WorkspacePaths
from ppt_agent.storage.zip_safety import (
    SafeZipMember,
    UnsafeZipEntry,
    validate_zip_members,
)

__all__ = [
    "SafeZipMember",
    "FilesystemKind",
    "UnsafeWorkspacePath",
    "UnsafeZipEntry",
    "WorkspaceCapabilities",
    "WorkspaceCapabilityError",
    "WorkspaceInstanceLock",
    "WorkspaceLockUnavailable",
    "WorkspacePaths",
    "classify_filesystem",
    "probe_workspace",
    "validate_zip_members",
]
