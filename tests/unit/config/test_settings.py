from pathlib import Path

import pytest

from ppt_agent.config.settings import SettingsError, load_settings


def test_precedence_cli_over_environment_over_toml(tmp_path: Path) -> None:
    config = tmp_path / "ppt-agent.toml"
    config.write_text('[server]\nport=7000\n[providers]\nmode="deterministic"\n', encoding="utf-8")
    settings = load_settings(config, environ={"PPT_AGENT_PORT": "7100"}, overrides={"port": 7200})
    assert settings.port == 7200
    assert settings.provider_mode == "deterministic"


def test_deterministic_mode_needs_no_keys_and_redacts_effective_view(tmp_path: Path) -> None:
    settings = load_settings(None, environ={"PPT_AGENT_WORKSPACE": str(tmp_path), "PPT_AGENT_PROVIDER_MODE": "deterministic"})
    assert settings.ark_api_key is None
    assert settings.redacted()["providers"]["ark_api_key"] is None


def test_cloud_mode_requires_one_ark_key_and_two_exact_model_ids(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="required"):
        load_settings(None, environ={"PPT_AGENT_WORKSPACE": str(tmp_path), "PPT_AGENT_PROVIDER_MODE": "cloud"})
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path),
        "PPT_AGENT_PROVIDER_MODE": "cloud",
        "ARK_API_KEY": "ark-secret-value",
        "PPT_AGENT_MULTIMODAL_MODEL_ID": "doubao-seed-2-1-pro-260915",
        "PPT_AGENT_IMAGE_MODEL_ID": "doubao-seedream-5-0-pro-260628",
    })
    assert settings.provider_mode == "cloud"
    assert settings.adapter_id == "volcengine_ark"
    assert settings.redacted()["providers"]["ark_api_key"] == "***configured***"


def test_provider_keys_support_secret_files_without_exposing_contents(tmp_path: Path) -> None:
    path = tmp_path / "ark.key"
    path.write_text("ark-secret-value\n", encoding="utf-8")
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path / "workspace"),
        "PPT_AGENT_PROVIDER_MODE": "cloud",
        "ARK_API_KEY_FILE": str(path),
        "PPT_AGENT_MULTIMODAL_MODEL_ID": "doubao-seed-2-1-pro-260915",
        "PPT_AGENT_IMAGE_MODEL_ID": "doubao-seedream-5-0-pro-260628",
    })

    assert settings.ark_api_key == "ark-secret-value"
    assert "ark-secret-value" not in str(settings.redacted())


def test_direct_provider_key_takes_precedence_over_secret_file(tmp_path: Path) -> None:
    key_file = tmp_path / "ark.key"
    key_file.write_text("file-secret", encoding="utf-8")
    settings = load_settings(None, environ={
        "ARK_API_KEY": "environment-secret",
        "ARK_API_KEY_FILE": str(key_file),
    })

    assert settings.ark_api_key == "environment-secret"
