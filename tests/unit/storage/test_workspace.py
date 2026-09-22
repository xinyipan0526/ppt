from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.storage.workspace import UnsafeWorkspacePath, WorkspacePaths


@pytest.fixture
def tmp_path() -> Path:
    path = Path(".cache/test-workspace-paths") / str(uuid4())
    path.mkdir(parents=True)
    return path.resolve()


def test_workspace_paths_resolve_nested_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths(workspace, create=True)

    resolved = paths.resolve("uploads/deck.pdf")

    assert paths.root == workspace.resolve()
    assert resolved == workspace.resolve() / "uploads" / "deck.pdf"


@pytest.mark.parametrize(
    "candidate",
    (
        "",
        ".",
        "./file.txt",
        "../outside.txt",
        r"..\outside.txt",
        "/tmp/outside",
        r"\rooted",
        r"C:\outside",
        r"C:relative",
        r"\\server\share\x",
    ),
)
def test_workspace_paths_reject_non_relative_or_escaping_names(
    tmp_path: Path, candidate: str
) -> None:
    paths = WorkspacePaths(tmp_path / "workspace", create=True)

    with pytest.raises(UnsafeWorkspacePath):
        paths.resolve(candidate)


def test_workspace_paths_require_existing_root_unless_create_requested(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        WorkspacePaths(tmp_path / "missing")


def test_workspace_paths_reject_file_as_root(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("file", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        WorkspacePaths(root)


def test_workspace_paths_reject_existing_link_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")

    paths = WorkspacePaths(workspace)

    with pytest.raises(UnsafeWorkspacePath, match="escapes"):
        paths.resolve("linked/payload.bin")


def test_workspace_paths_verify_detects_parent_replaced_by_link(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    parent = workspace / "staging"
    parent.mkdir(parents=True)
    outside.mkdir()
    paths = WorkspacePaths(workspace)
    target = paths.resolve("staging/payload.bin")
    parent.rmdir()
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        parent.mkdir()
        pytest.skip(f"directory links are unavailable: {exc}")
    (outside / "payload.bin").write_bytes(b"escaped")

    with pytest.raises(UnsafeWorkspacePath, match="escapes"):
        paths.verify(target)


def test_workspace_paths_verify_requires_existing_target(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "workspace", create=True)
    target = paths.resolve("missing.bin")

    with pytest.raises(FileNotFoundError):
        paths.verify(target)
