"""Redacted Provider capability and explicit connection-test endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.config.settings import Settings


ConnectionTest = Callable[[], Awaitable[dict[str, object]]]


def providers_router(
    *, settings: Settings | None = None,
    resolve_settings: Callable[[], Settings] | None = None,
    security: LocalHTTPSecurity,
    connection_tests: dict[str, ConnectionTest] | None = None,
) -> APIRouter:
    if (settings is None) == (resolve_settings is None):
        raise ValueError("provide exactly one settings source")
    current = resolve_settings or (lambda: settings)  # type: ignore[return-value]
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])
    tests = connection_tests or {}

    @router.get("/providers")
    async def provider_status() -> dict:
        settings = current()
        return {
            "mode": settings.provider_mode,
            "providers": {
                "deepseek": {"configured": settings.provider_mode == "deterministic" or bool(settings.deepseek_api_key), "model": settings.deepseek_model},
                "seedream": {"configured": settings.provider_mode == "deterministic" or bool(settings.seedream_api_key), "model": settings.seedream_model, "generation": True, "reference_edit": True},
                "qwen": {"configured": settings.provider_mode == "deterministic" or bool(settings.qwen_api_key), "vision_model": settings.qwen_vision_model, "ocr_model": settings.qwen_ocr_model},
            },
        }

    @router.post("/providers/{provider}/test")
    async def test_provider(provider: str) -> dict:
        settings = current()
        if settings.provider_mode == "deterministic":
            if provider not in {"deepseek", "seedream", "qwen"}:
                raise HTTPException(status_code=404, detail="provider not found")
            return {"provider": provider, "status": "ok", "mode": "deterministic"}
        callback = tests.get(provider)
        if callback is None:
            raise HTTPException(status_code=404, detail="provider not found")
        try:
            result = await callback()
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"code": "provider_connection_failed", "type": type(exc).__name__}) from exc
        return {"provider": provider, "status": "ok", **result}

    return router
