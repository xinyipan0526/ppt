from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import ppt_agent.storage.capabilities as capability_module
from ppt_agent.storage.capabilities import (
    FilesystemKind,
    WorkspaceCapabilityError,
    classify_filesystem,
    probe_workspace,
)


@pytest.fixture
def workspace() -> Path:
    return (Path(".cache/test-workspace-capabilities") / str(uuid4())).resolve()


def test_probe_verifies_real_local_workspace_and_cleans_probe_directory(
    workspace: Path,
) -> None:
    result = probe_workspace(
        workspace,
        minimum_free_bytes=0,
        classifier=lambda _: FilesystemKind.LOCAL,
    )

    assert result.root == workspace
    assert result.filesystem_kind is FilesystemKind.LOCAL
    assert result.atomic_replace is True
    assert result.exclusive_lock is True
    assert result.file_fsync is True
    assert result.directory_fsync in {True, None}
    assert result.same_device is True
    assert result.free_bytes > 0
    assert result.journal_mode in {"WAL", "DELETE"}
    assert not tuple(workspace.glob(".capability-probe-*"))


def test_probe_rejects_known_network_storage(workspace: Path) -> None:
    with pytest.raises(WorkspaceCapabilityError, match="network"):
        probe_workspace(
            workspace,
            minimum_free_bytes=0,
            classifier=lambda _: FilesystemKind.NETWORK,
        )


def test_probe_classifies_canonical_workspace_root(workspace: Path) -> None:
    requested = workspace / "parent" / ".." / "canonical"
    classified: list[Path] = []

    def classifier(path: Path) -> FilesystemKind:
        classified.append(path)
        return FilesystemKind.LOCAL

    result = probe_workspace(requested, minimum_free_bytes=0, classifier=classifier)

    assert classified == [requested.resolve()]
    assert result.root == requested.resolve()


def test_network_classification_takes_priority_over_sync_directory_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        capability_module,
        "_classify_windows_filesystem",
        lambda _: FilesystemKind.NETWORK,
    )

    result = classify_filesystem(Path(r"C:\Users\person\OneDrive\workspace"))

    assert result is FilesystemKind.NETWORK


def test_probe_warns_for_sync_storage(workspace: Path) -> None:
    result = probe_workspace(
        workspace,
        minimum_free_bytes=0,
        classifier=lambda _: FilesystemKind.SYNC,
    )

    assert any("synchron" in warning.lower() for warning in result.warnings)


def test_probe_rejects_unknown_storage_in_production(workspace: Path) -> None:
    with pytest.raises(WorkspaceCapabilityError, match="classify"):
        probe_workspace(
            workspace,
            minimum_free_bytes=0,
            classifier=lambda _: FilesystemKind.UNKNOWN,
        )


def test_probe_allows_unknown_storage_only_outside_production(workspace: Path) -> None:
    result = probe_workspace(
        workspace,
        production=False,
        minimum_free_bytes=0,
        classifier=lambda _: FilesystemKind.UNKNOWN,
    )

    assert any("unverified" in warning.lower() for warning in result.warnings)


def test_probe_uses_delete_journal_when_wal_is_unavailable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capability_module, "_probe_wal", lambda _: "DELETE")
    monkeypatch.setattr(capability_module, "_probe_directory_fsync", lambda _: True)

    result = probe_workspace(
        workspace,
        minimum_free_bytes=0,
        classifier=lambda _: FilesystemKind.LOCAL,
    )

    assert result.journal_mode == "DELETE"
    assert any("wal" in warning.lower() for warning in result.warnings)


def test_probe_rejects_delete_fallback_without_verified_directory_fsync(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capability_module, "_probe_wal", lambda _: "DELETE")
    monkeypatch.setattr(capability_module, "_probe_directory_fsync", lambda _: None)

    with pytest.raises(WorkspaceCapabilityError, match="directory fsync"):
        probe_workspace(
            workspace,
            minimum_free_bytes=0,
            classifier=lambda _: FilesystemKind.LOCAL,
        )


def test_probe_rejects_insufficient_free_space(workspace: Path) -> None:
    with pytest.raises(WorkspaceCapabilityError, match="free space"):
        probe_workspace(
            workspace,
            minimum_free_bytes=2**63,
            classifier=lambda _: FilesystemKind.LOCAL,
        )


@pytest.mark.parametrize(
    ("helper_name", "message"),
    (
        ("_probe_atomic_replace", "atomic replace"),
        ("_probe_exclusive_lock", "exclusive lock"),
        ("_probe_file_fsync", "file fsync"),
    ),
)
def test_probe_fails_closed_and_cleans_up_when_required_capability_fails(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    message: str,
) -> None:
    monkeypatch.setattr(capability_module, helper_name, lambda _: False)

    with pytest.raises(WorkspaceCapabilityError, match=message):
        probe_workspace(
            workspace,
            minimum_free_bytes=0,
            classifier=lambda _: FilesystemKind.LOCAL,
        )

    assert not tuple(workspace.glob(".capability-probe-*"))


def test_capabilities_build_database_config_only_below_workspace(
    workspace: Path,
) -> None:
    result = probe_workspace(
        workspace,
        minimum_free_bytes=0,
        classifier=lambda _: FilesystemKind.LOCAL,
    )

    config = result.database_config(
        workspace / "state.db",
        busy_timeout_ms=7_500,
        wal_autocheckpoint=321,
    )

    assert config.path == workspace / "state.db"
    assert config.journal_mode == result.journal_mode
    assert config.busy_timeout_ms == 7_500
    assert config.wal_autocheckpoint == 321
    with pytest.raises(WorkspaceCapabilityError, match="below"):
        result.database_config(workspace.parent / "outside.db")
