import asyncio

from httpx import ASGITransport, AsyncClient

from ppt_agent.api.app import create_app


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
    app = create_app()

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
