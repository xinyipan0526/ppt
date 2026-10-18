import asyncio
import logging
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.settings import Settings
from ppt_agent.runtime.provider_config import ProviderConfigurationService


def test_health_endpoint_reports_service_identity() -> None:
    async def request_health() -> tuple[int, dict[str, str]]:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get("/api/v1/health")
        return response.status_code, response.json()

    status_code, payload = asyncio.run(request_health())

    assert status_code == 200
    assert payload == {
        "status": "ok",
        "service": "ppt-agent",
        "version": "0.1.0",
    }


def test_application_metadata_is_stable() -> None:
    app = create_app(security=LocalHTTPSecurity(token="t" * 43))

    assert app.title == "PPT Agent API"
    assert app.version == "0.1.0"


def test_readiness_stays_closed_without_runtime() -> None:
    async def request_readiness() -> tuple[int, dict[str, str]]:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get("/api/v1/ready")
        return response.status_code, response.json()

    status_code, payload = asyncio.run(request_readiness())

    assert status_code == 503
    assert payload == {"status": "not_ready"}


def test_unhandled_api_error_uses_safe_correlation_response_and_logs_it(caplog) -> None:
    app = create_app()

    @app.get("/test-unhandled-error")
    async def fail() -> None:
        raise RuntimeError("provider payload ark-secret C:/private/workspace")

    async def request_error() -> tuple[int, dict[str, object], str | None]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get("/test-unhandled-error")
        return response.status_code, response.json(), response.headers.get("X-Correlation-ID")

    with caplog.at_level(logging.ERROR, logger="ppt_agent.api"):
        status_code, payload, header_correlation_id = asyncio.run(request_error())

    assert status_code == 500
    assert payload["detail"]["code"] == "internal_error"
    assert payload["detail"]["correlation_id"] == header_correlation_id
    assert "ark-secret" not in str(payload)
    assert "C:/private" not in str(payload)
    record = next(
        record for record in caplog.records if record.name == "ppt_agent.api"
    )
    assert record.correlation_id == header_correlation_id
    assert record.exc_info is not None


def test_provider_settings_exposes_real_cloud_setup_intent_without_a_key(
    tmp_path: Path,
) -> None:
    app = create_app(security=LocalHTTPSecurity(token="t" * 43))
    app.state.provider_configuration = ProviderConfigurationService(
        Settings(workspace=tmp_path, provider_setup_required=True),
        FileProviderSecretStore(tmp_path / ".secrets" / "adapters"),
        is_idle=lambda: True,
        activate=lambda _: None,
    )

    async def request_settings() -> tuple[int, dict[str, object]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.get(
                "/api/v1/provider-settings",
                headers={"Authorization": "Bearer " + "t" * 43},
            )
        return response.status_code, response.json()

    status_code, payload = asyncio.run(request_settings())

    assert status_code == 200
    assert payload == {
        "mode": "deterministic",
        "configured": False,
        "setup_required": True,
    }
