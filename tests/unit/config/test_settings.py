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


def test_cloud_mode_without_complete_credentials_preserves_setup_intent(tmp_path: Path) -> None:
    pending = load_settings(
        None,
        environ={
            "PPT_AGENT_WORKSPACE": str(tmp_path),
            "PPT_AGENT_PROVIDER_MODE": "cloud",
        },
    )
    assert pending.provider_mode == "deterministic"
    assert pending.provider_setup_required is True
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path),
        "PPT_AGENT_PROVIDER_MODE": "cloud",
        "ARK_API_KEY": "ark-secret-value",
        "PPT_AGENT_MULTIMODAL_MODEL_ID": "doubao-seed-2-1-pro-260915",
        "PPT_AGENT_IMAGE_MODEL_ID": "doubao-seedream-5-0-pro-260628",
    })
    assert settings.provider_mode == "cloud"
    assert settings.provider_setup_required is False
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


def test_automatic_backup_defaults_and_configuration_sources(tmp_path: Path) -> None:
    defaults = load_settings(None, environ={})
    assert defaults.backup_enabled is True
    assert defaults.backup_interval_seconds == 86_400
    assert defaults.backup_retention == 7
    assert defaults.backup_shutdown_timeout_seconds == 5

    config = tmp_path / "ppt-agent.toml"
    config.write_text(
        "[backup]\n"
        "enabled=false\n"
        "interval_seconds=120\n"
        "retention=3\n"
        "shutdown_timeout_seconds=2\n",
        encoding="utf-8",
    )
    configured = load_settings(
        config,
        environ={"PPT_AGENT_BACKUP_RETENTION": "4"},
    )
    assert configured.backup_enabled is False
    assert configured.backup_interval_seconds == 120
    assert configured.backup_retention == 4
    assert configured.backup_shutdown_timeout_seconds == 2


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("PPT_AGENT_BACKUP_ENABLED", "sometimes"),
        ("PPT_AGENT_BACKUP_INTERVAL_SECONDS", "0"),
        ("PPT_AGENT_BACKUP_RETENTION", "0"),
        ("PPT_AGENT_BACKUP_SHUTDOWN_TIMEOUT_SECONDS", "-0.1"),
    ),
)
def test_automatic_backup_settings_reject_invalid_values(
    name: str, value: str
) -> None:
    with pytest.raises(SettingsError):
        load_settings(None, environ={name: value})
