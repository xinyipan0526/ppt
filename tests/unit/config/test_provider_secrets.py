from pathlib import Path

import pytest

from ppt_agent.config.provider_secrets import FileProviderSecretStore, ProviderSecretError


def test_file_provider_secret_store_replaces_values_without_exposing_them(tmp_path: Path) -> None:
    store = FileProviderSecretStore(tmp_path / "secrets")

    store.replace("deepseek", "deepseek-super-secret")
    first = store.status("deepseek")
    store.replace("deepseek", "replacement-secret-7890")
    second = store.status("deepseek")

    assert first.configured is True
    assert first.backend == "workspace_secret_file"
    assert first.masked_suffix == "cret"
    assert second.masked_suffix == "7890"
    assert store.load("deepseek") == "replacement-secret-7890"
    assert "replacement-secret" not in repr(second)


def test_file_provider_secret_store_rejects_unknown_names_and_symlink_targets(tmp_path: Path) -> None:
    store = FileProviderSecretStore(tmp_path / "secrets")

    with pytest.raises(ProviderSecretError, match="unsupported"):
        store.replace("other", "a-valid-secret")

    store.root.mkdir(parents=True)
    target = tmp_path / "outside"
    target.write_text("outside-value", encoding="utf-8")
    try:
        (store.root / "deepseek.key").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ProviderSecretError, match="symbolic"):
        store.replace("deepseek", "replacement-secret")
    assert target.read_text(encoding="utf-8") == "outside-value"


def test_file_provider_secret_delete_is_idempotent(tmp_path: Path) -> None:
    store = FileProviderSecretStore(tmp_path / "secrets")
    store.replace("qwen", "qwen-secret-value")

    assert store.delete("qwen") is True
    assert store.delete("qwen") is False
    assert store.status("qwen").configured is False
