from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.storage.locking import (
    WorkspaceInstanceLock,
    WorkspaceLockUnavailable,
)
from ppt_agent.storage.workspace import WorkspacePaths


@pytest.fixture
def workspace_paths() -> WorkspacePaths:
    root = Path(".cache/test-workspace-locks") / str(uuid4())
    return WorkspacePaths(root, create=True)


def test_workspace_lock_writes_owner_metadata_after_acquisition(
    workspace_paths: WorkspacePaths,
) -> None:
    lock = WorkspaceInstanceLock(workspace_paths)
    metadata_path = workspace_paths.root / ".ppt-agent.lock"

    assert not metadata_path.exists()
    lock.acquire()
    lock.release()
    metadata = json.loads(metadata_path.read_text())

    assert metadata["pid"] > 0
    assert metadata["pid"] == __import__("os").getpid()
    assert metadata["acquired_at"].endswith("+00:00")


def test_workspace_lock_can_be_reacquired_after_release(
    workspace_paths: WorkspacePaths,
) -> None:
    first = WorkspaceInstanceLock(workspace_paths)
    second = WorkspaceInstanceLock(workspace_paths)

    first.acquire()
    first.release()
    second.acquire()
    second.release()


def test_workspace_lock_rejects_invalid_lifecycle(
    workspace_paths: WorkspacePaths,
) -> None:
    lock = WorkspaceInstanceLock(workspace_paths)

    with pytest.raises(RuntimeError, match="not acquired"):
        lock.release()
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()


def test_workspace_lock_context_manager_releases_after_error(
    workspace_paths: WorkspacePaths,
) -> None:
    with pytest.raises(ValueError, match="stop"):
        with WorkspaceInstanceLock(workspace_paths):
            raise ValueError("stop")

    with WorkspaceInstanceLock(workspace_paths):
        pass


def test_workspace_lock_excludes_another_process(
    workspace_paths: WorkspacePaths,
) -> None:
    with WorkspaceInstanceLock(workspace_paths):
        blocked = _run_lock_probe(workspace_paths.root)
    acquired = _run_lock_probe(workspace_paths.root)

    assert blocked.returncode == 23, blocked.stderr
    assert acquired.returncode == 0, acquired.stderr


def _run_lock_probe(root: Path) -> subprocess.CompletedProcess[str]:
    source = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
from ppt_agent.storage.locking import WorkspaceInstanceLock, WorkspaceLockUnavailable
from ppt_agent.storage.workspace import WorkspacePaths
lock = WorkspaceInstanceLock(WorkspacePaths(Path(sys.argv[1])))
try:
    lock.acquire()
except WorkspaceLockUnavailable:
    raise SystemExit(23)
else:
    lock.release()
"""
    return subprocess.run(
        [sys.executable, "-c", source, str(root)],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
