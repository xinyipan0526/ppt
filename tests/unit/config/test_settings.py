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
    assert settings.deepseek_api_key is None
    assert settings.redacted()["providers"]["deepseek_api_key"] is None


def test_cloud_mode_requires_https_and_all_keys(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="required"):
        load_settings(None, environ={"PPT_AGENT_WORKSPACE": str(tmp_path), "PPT_AGENT_PROVIDER_MODE": "cloud"})
    with pytest.raises(SettingsError, match="HTTPS"):
        load_settings(None, environ={
            "PPT_AGENT_WORKSPACE": str(tmp_path), "PPT_AGENT_PROVIDER_MODE": "cloud",
            "DEEPSEEK_API_KEY": "deep-secret", "SEEDREAM_API_KEY": "seed-secret", "QWEN_API_KEY": "qwen-secret",
            "PPT_AGENT_SEEDREAM_BASE_URL": "http://example.test/v1",
        })


def test_cloud_mode_requires_explicit_positive_pricing(tmp_path: Path) -> None:
    common = {
        "PPT_AGENT_WORKSPACE": str(tmp_path),
        "PPT_AGENT_PROVIDER_MODE": "cloud",
        "DEEPSEEK_API_KEY": "deep-secret",
        "SEEDREAM_API_KEY": "seed-secret",
        "QWEN_API_KEY": "qwen-secret",
        "PPT_AGENT_SEEDREAM_RESULT_HOSTS": "image.example.test",
    }
    with pytest.raises(SettingsError, match="positive cloud pricing"):
        load_settings(None, environ=common)

    settings = load_settings(None, environ={
        **common,
        "PPT_AGENT_PRICING_VERSION": "prices-2026-09-30",
        "PPT_AGENT_DEEPSEEK_INPUT_PER_MILLION": "100",
        "PPT_AGENT_DEEPSEEK_OUTPUT_PER_MILLION": "200",
        "PPT_AGENT_SEEDREAM_PER_IMAGE": "300",
        "PPT_AGENT_QWEN_INPUT_PER_MILLION": "400",
        "PPT_AGENT_QWEN_OUTPUT_PER_MILLION": "500",
    })
    assert settings.seedream_per_image == 300
    assert settings.redacted()["providers"]["pricing"]["version"] == "prices-2026-09-30"


def test_provider_keys_support_secret_files_without_exposing_contents(tmp_path: Path) -> None:
    key_files = {}
    for provider in ("DEEPSEEK", "SEEDREAM", "QWEN"):
        path = tmp_path / f"{provider.casefold()}.key"
        path.write_text(f"{provider.casefold()}-secret\n", encoding="utf-8")
        key_files[f"{provider}_API_KEY_FILE"] = str(path)
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path / "workspace"),
        "PPT_AGENT_PROVIDER_MODE": "cloud",
        "PPT_AGENT_SEEDREAM_RESULT_HOSTS": "example.com",
        "PPT_AGENT_DEEPSEEK_INPUT_PER_MILLION": "1",
        "PPT_AGENT_DEEPSEEK_OUTPUT_PER_MILLION": "1",
        "PPT_AGENT_SEEDREAM_PER_IMAGE": "1",
        "PPT_AGENT_QWEN_INPUT_PER_MILLION": "1",
        "PPT_AGENT_QWEN_OUTPUT_PER_MILLION": "1",
        **key_files,
    })

    assert settings.deepseek_api_key == "deepseek-secret"
    assert settings.seedream_api_key == "seedream-secret"
    assert settings.qwen_api_key == "qwen-secret"
    assert "deepseek-secret" not in str(settings.redacted())


def test_direct_provider_key_takes_precedence_over_secret_file(tmp_path: Path) -> None:
    key_file = tmp_path / "deepseek.key"
    key_file.write_text("file-secret", encoding="utf-8")
    settings = load_settings(None, environ={
        "DEEPSEEK_API_KEY": "environment-secret",
        "DEEPSEEK_API_KEY_FILE": str(key_file),
    })

    assert settings.deepseek_api_key == "environment-secret"
