from pathlib import Path

from fastapi.testclient import TestClient

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.api.settings import provider_settings_router
from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.settings import Settings
from ppt_agent.runtime.provider_config import ProviderConfigurationService
from ppt_agent.runtime.composition import build_application


def test_provider_settings_accept_secrets_without_returning_them(tmp_path: Path) -> None:
    applied: list[Settings] = []
    service = ProviderConfigurationService(
        Settings(workspace=tmp_path),
        FileProviderSecretStore(tmp_path / ".secrets" / "providers"),
        is_idle=lambda: True,
        activate=lambda settings: applied.append(settings),
    )
    security = LocalHTTPSecurity(token="t" * 43)
    app = create_app(security=security)
    app.include_router(provider_settings_router(service=service, security=security))
    headers = {"Origin": "http://localhost:8000", "Authorization": "Bearer " + "t" * 43}
    payload = {
        "deepseek_api_key": "deepseek-browser-secret",
        "seedream_api_key": "seedream-browser-secret",
        "qwen_api_key": "qwen-browser-secret",
        "seedream_result_hosts": ["image.example.test"],
        "pricing_version": "ui-2026-10-01",
        "deepseek_input_per_million": 100,
        "deepseek_output_per_million": 200,
        "seedream_per_image": 300,
        "qwen_input_per_million": 400,
        "qwen_output_per_million": 500,
    }

    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.put("/api/v1/settings/providers", headers=headers, json=payload)
        status = client.get(
            "/api/v1/settings/providers", headers={"Authorization": "Bearer " + "t" * 43}
        )

    combined = response.text + status.text
    assert response.status_code == 200
    assert status.status_code == 200
    assert response.json()["mode"] == "cloud"
    assert response.json()["activation"] == "active"
    assert set(response.json()["providers"]) == {"deepseek", "seedream", "qwen"}
    assert all(item["configured"] for item in response.json()["providers"].values())
    assert not any(secret in combined for secret in payload.values() if isinstance(secret, str) and "secret" in secret)
    assert len(applied) == 1
    assert applied[0].deepseek_api_key == "deepseek-browser-secret"


def test_provider_settings_defer_activation_while_runtime_is_busy(tmp_path: Path) -> None:
    applied: list[Settings] = []
    service = ProviderConfigurationService(
        Settings(workspace=tmp_path),
        FileProviderSecretStore(tmp_path / ".secrets" / "providers"),
        is_idle=lambda: False,
        activate=lambda settings: applied.append(settings),
    )
    for name in ("deepseek", "seedream", "qwen"):
        service.secrets.replace(name, f"{name}-secret-value")

    snapshot = service.update(
        seedream_result_hosts=("image.example.test",),
        pricing_version="ui-v1",
        deepseek_input_per_million=1,
        deepseek_output_per_million=1,
        seedream_per_image=1,
        qwen_input_per_million=1,
        qwen_output_per_million=1,
    )

    assert snapshot.activation == "pending"
    assert applied == []


def test_provider_configuration_preserves_complete_process_configuration(
    tmp_path: Path,
) -> None:
    settings = Settings(
        workspace=tmp_path,
        provider_mode="cloud",
        deepseek_api_key="deepseek-process-secret",
        seedream_api_key="seedream-process-secret",
        qwen_api_key="qwen-process-secret",
        seedream_result_hosts=("image.example.test",),
        pricing_version="env-v1",
        deepseek_input_per_million=1,
        deepseek_output_per_million=1,
        seedream_per_image=1,
        qwen_input_per_million=1,
        qwen_output_per_million=1,
    )
    service = ProviderConfigurationService(
        settings,
        FileProviderSecretStore(tmp_path / ".secrets" / "providers"),
        is_idle=lambda: True,
        activate=lambda candidate: None,
    )

    snapshot = service.snapshot()

    assert service.settings.provider_mode == "cloud"
    assert all(item["configured"] for item in snapshot.providers.values())
    assert {item["backend"] for item in snapshot.providers.values()} == {
        "process_configuration"
    }


def test_complete_application_activates_and_reloads_browser_provider_configuration(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    initial = Settings(workspace=workspace, local_access_token="t" * 43)
    payload = {
        "deepseek_api_key": "deepseek-browser-secret",
        "seedream_api_key": "seedream-browser-secret",
        "qwen_api_key": "qwen-browser-secret",
        "seedream_result_hosts": ["image.example.test"],
        "pricing_version": "ui-2026-10-01",
        "deepseek_input_per_million": 100,
        "deepseek_output_per_million": 200,
        "seedream_per_image": 300,
        "qwen_input_per_million": 400,
        "qwen_output_per_million": 500,
    }
    headers = {"Origin": "http://localhost:8000", "Authorization": "Bearer " + "t" * 43}

    with TestClient(build_application(initial), base_url="http://localhost:8000") as client:
        configured = client.put("/api/v1/settings/providers", headers=headers, json=payload)
        providers = client.get(
            "/api/v1/providers", headers={"Authorization": "Bearer " + "t" * 43}
        )

    with TestClient(build_application(initial), base_url="http://localhost:8000") as client:
        restored = client.get(
            "/api/v1/settings/providers", headers={"Authorization": "Bearer " + "t" * 43}
        )

    assert configured.status_code == 200
    assert configured.json()["activation"] == "active"
    assert providers.json()["mode"] == "cloud"
    assert restored.json()["mode"] == "cloud"
    assert all(item["configured"] for item in restored.json()["providers"].values())
