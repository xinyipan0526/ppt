import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from ppt_agent.api.app import create_app
from ppt_agent.api.security import (
    AccessTokenManager,
    InputLimits,
    LocalHTTPConfig,
    LocalHTTPSecurity,
    SecretFileTokenStore,
    TokenStorageUnavailable,
)


class MemoryTokenStore:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.saved: list[str] = []

    def load(self) -> str | None:
        return self.value

    def save(self, token: str) -> None:
        self.value = token
        self.saved.append(token)


def test_access_token_is_generated_once_with_high_entropy() -> None:
    store = MemoryTokenStore()
    manager = AccessTokenManager(store)

    first = manager.load_or_create()
    second = manager.load_or_create()

    assert first == second
    assert len(first) >= 43
    assert store.saved == [first]


def test_application_composition_initializes_token_manager() -> None:
    store = MemoryTokenStore()
    create_app(token_manager=AccessTokenManager(store))

    assert len(store.saved) == 1
    assert len(store.saved[0]) >= 43


def test_input_limits_reject_non_positive_or_inverted_sizes() -> None:
    with pytest.raises(ValueError, match="positive"):
        InputLimits(max_pdf_pages=0)
    with pytest.raises(ValueError, match="single-file"):
        InputLimits(max_file_bytes=2, max_project_upload_bytes=1)


def test_secret_file_store_is_read_only_and_rejects_short_secret() -> None:
    directory = Path(".cache/test-api-security") / str(uuid4())
    directory.mkdir(parents=True)
    secret = directory / "token"
    secret.write_text("short", encoding="utf-8")
    store = SecretFileTokenStore(secret)

    with pytest.raises(TokenStorageUnavailable, match="at least"):
        store.load()
    with pytest.raises(TokenStorageUnavailable, match="read-only"):
        store.save("x" * 43)


def test_sensitive_route_requires_bearer_and_transport_boundary() -> None:
    async def scenario() -> tuple[int, int, int, int, int, int]:
        security = LocalHTTPSecurity(
            token="t" * 43,
            config=LocalHTTPConfig(
                allowed_authorities=frozenset({"localhost:8000", "[::1]:8000"}),
                allowed_origins=frozenset({"http://localhost:3000"}),
            ),
        )
        app = create_app(security=security)

        @app.post("/api/v1/protected", dependencies=[Depends(security.authenticate)])
        async def protected() -> dict[str, bool]:
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            missing = await client.post(
                "/api/v1/protected", headers={"Origin": "http://localhost:3000"}
            )
            wrong = await client.post(
                "/api/v1/protected",
                headers={
                    "Origin": "http://localhost:3000",
                    "Authorization": "Bearer wrong",
                },
            )
            query = await client.post(
                "/api/v1/protected?access_token=" + "t" * 43,
                headers={"Origin": "http://localhost:3000"},
            )
            no_origin = await client.post(
                "/api/v1/protected", headers={"Authorization": "Bearer " + "t" * 43}
            )
            foreign_origin = await client.post(
                "/api/v1/protected",
                headers={
                    "Origin": "https://evil.example",
                    "Authorization": "Bearer " + "t" * 43,
                },
            )
            accepted = await client.post(
                "/api/v1/protected",
                headers={
                    "Origin": "http://localhost:3000",
                    "Authorization": "Bearer " + "t" * 43,
                },
            )
        return (
            missing.status_code,
            wrong.status_code,
            query.status_code,
            no_origin.status_code,
            foreign_origin.status_code,
            accepted.status_code,
        )

    assert asyncio.run(scenario()) == (401, 401, 401, 403, 403, 200)


def test_invalid_host_is_rejected_before_authentication() -> None:
    async def scenario() -> tuple[int, str]:
        security = LocalHTTPSecurity(token="t" * 43)
        app = create_app(security=security)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://evil.example") as client:
            response = await client.get(
                "/api/v1/health", headers={"Authorization": "Bearer " + "t" * 43}
            )
        return response.status_code, response.text

    status, body = asyncio.run(scenario())

    assert status == 400
    assert "evil.example" not in body
