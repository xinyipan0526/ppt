from pathlib import Path

from fastapi.testclient import TestClient

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.api.settings import provider_settings_router
from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.settings import Settings
from ppt_agent.runtime.provider_config import ProviderConfigurationService


MULTIMODAL = "doubao-seed-2-1-pro-260915"
IMAGE = "doubao-seedream-5-0-pro-260628"


def _client(tmp_path: Path, *, idle: bool = True):
    service = ProviderConfigurationService(
        Settings(workspace=tmp_path),
        FileProviderSecretStore(tmp_path / ".secrets" / "adapters"),
        is_idle=lambda: idle,
        activate=lambda _: None,
        settings_path=tmp_path / "config" / "provider-settings.json",
    )
    security = LocalHTTPSecurity(token="t" * 43)
    app = create_app(security=security)
    app.include_router(provider_settings_router(service=service, security=security))
    return app, service


def _headers(*, origin: bool = False) -> dict[str, str]:
    headers = {"Authorization": "Bearer " + "t" * 43}
    if origin:
        headers["Origin"] = "http://localhost:8000"
    return headers


def test_model_runtime_accepts_only_key_and_exact_model_ids_without_disclosure(
    tmp_path: Path,
) -> None:
    app, service = _client(tmp_path)
    payload = {
        "ark_api_key": "ark-browser-secret",
        "multimodal_model_id": MULTIMODAL,
        "image_model_id": IMAGE,
    }

    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.put(
            "/api/v1/settings/model-runtime",
            headers=_headers(origin=True),
            json=payload,
        )
        status = client.get(
            "/api/v1/settings/model-runtime", headers=_headers()
        )

    combined = response.text + status.text
    assert response.status_code == 200
    assert response.json()["mode"] == "cloud"
    assert response.json()["multimodal_model"]["model_id"] == MULTIMODAL
    assert response.json()["image_model"]["model_id"] == IMAGE
    assert "ark-browser-secret" not in combined
    assert service.secrets.load("volcengine_ark") == "ark-browser-secret"


def test_invalid_category_is_rejected_before_secret_replacement(tmp_path: Path) -> None:
    app, service = _client(tmp_path)
    service.secrets.replace("volcengine_ark", "previous-secret")

    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.put(
            "/api/v1/settings/model-runtime",
            headers=_headers(origin=True),
            json={
                "ark_api_key": "candidate-secret",
                "multimodal_model_id": IMAGE,
                "image_model_id": IMAGE,
            },
        )

    assert response.status_code == 422
    assert service.secrets.load("volcengine_ark") == "previous-secret"


def test_busy_runtime_reports_pending_and_legacy_endpoint_is_deprecated(
    tmp_path: Path,
) -> None:
    app, _ = _client(tmp_path, idle=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.put(
            "/api/v1/settings/model-runtime",
            headers=_headers(origin=True),
            json={
                "ark_api_key": "ark-browser-secret",
                "multimodal_model_id": MULTIMODAL,
                "image_model_id": IMAGE,
            },
        )
        legacy = client.put(
            "/api/v1/settings/providers", headers=_headers(origin=True), json={}
        )

    assert response.json()["activation"] == "pending"
    assert legacy.status_code == 410
