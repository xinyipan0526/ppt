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
    tested: list[tuple[str, str, str]] = []

    async def connection_test(api_key: str, multimodal: str, image: str):
        tested.append((api_key, multimodal, image))
        return {"status": "ok"}

    app.include_router(provider_settings_router(
        service=service, security=security, connection_test=connection_test
    ))
    app.state.tested = tested
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


def test_catalog_returns_exact_select_values_and_default_pricing(tmp_path: Path) -> None:
    app, _ = _client(tmp_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.get("/api/v1/model-catalog", headers=_headers())

    values = {item["model_id"]: item for item in response.json()}
    assert response.status_code == 200
    assert values[MULTIMODAL]["category"] == "multimodal"
    assert values[MULTIMODAL]["pricing"]["input_per_million"] == 600
    assert values[IMAGE]["category"] == "image"
    assert values[IMAGE]["pricing"]["output_up_to_2_61mp"] == 30
    assert "Doubao Seed 2.1 Pro" not in values


def test_connection_test_does_not_mutate_active_secret(tmp_path: Path) -> None:
    app, service = _client(tmp_path)
    service.secrets.replace("volcengine_ark", "previous-secret")
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post(
            "/api/v1/adapters/volcengine_ark/test",
            headers=_headers(origin=True),
            json={
                "ark_api_key": "candidate-secret",
                "multimodal_model_id": MULTIMODAL,
                "image_model_id": IMAGE,
            },
        )

    assert response.status_code == 200
    assert app.state.tested == [("candidate-secret", MULTIMODAL, IMAGE)]
    assert service.secrets.load("volcengine_ark") == "previous-secret"
    assert "candidate-secret" not in response.text


def test_credential_delete_returns_runtime_to_deterministic(tmp_path: Path) -> None:
    app, service = _client(tmp_path)
    service.update(
        ark_api_key="ark-browser-secret",
        multimodal_model_id=MULTIMODAL,
        image_model_id=IMAGE,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.delete(
            "/api/v1/adapters/volcengine_ark/credential",
            headers=_headers(origin=True),
        )

    assert response.status_code == 200
    assert response.json()["mode"] == "deterministic"
    assert response.json()["adapter_status"]["configured"] is False
