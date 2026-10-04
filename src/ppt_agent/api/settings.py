"""Redacted browser configuration endpoints for the model runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.config.provider_secrets import ProviderSecretError
from ppt_agent.config.settings import SettingsError
from ppt_agent.runtime.provider_config import ProviderConfigurationService
from ppt_agent.providers.capabilities import ModelCategory
from ppt_agent.providers.catalog import ARK_ADAPTER_ID, DEFAULT_MODEL_CATALOG


ConnectionTest = Callable[[str, str, str], Awaitable[dict[str, object]]]


class ModelRuntimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ark_api_key: SecretStr | None = None
    multimodal_model_id: str = Field(min_length=1, max_length=200)
    image_model_id: str = Field(min_length=1, max_length=200)
    pricing_overrides: dict[str, float | int] | None = None


def provider_settings_router(
    *,
    service: ProviderConfigurationService,
    security: LocalHTTPSecurity,
    connection_test: ConnectionTest | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(security.authenticate)])

    async def qualify(request: ModelRuntimeUpdate) -> dict[str, object]:
        DEFAULT_MODEL_CATALOG.require(
            request.multimodal_model_id, ModelCategory.MULTIMODAL
        )
        DEFAULT_MODEL_CATALOG.require(request.image_model_id, ModelCategory.IMAGE)
        candidate_key = (
            request.ark_api_key.get_secret_value()
            if request.ark_api_key is not None
            else service.secrets.load(ARK_ADAPTER_ID) or service.settings.ark_api_key
        )
        if not candidate_key:
            raise SettingsError("Ark API key is required")
        if connection_test is None:
            return {"status": "configuration_valid"}
        return await connection_test(
            candidate_key,
            request.multimodal_model_id,
            request.image_model_id,
        )

    def safe_connection_code(exc: Exception) -> str:
        safe_codes = {
            "authentication_error",
            "model_unavailable",
            "capability_mismatch",
            "unknown_remote_outcome",
        }
        code = getattr(exc, "code", "unknown_remote_outcome")
        return code if code in safe_codes else "unknown_remote_outcome"

    @router.get("/model-catalog")
    async def get_model_catalog() -> tuple[dict[str, object], ...]:
        return DEFAULT_MODEL_CATALOG.public_snapshot()

    @router.get("/settings/model-runtime")
    async def get_settings() -> dict[str, object]:
        return service.activate_if_idle().public_projection()

    @router.put("/settings/model-runtime")
    async def update_settings(request: ModelRuntimeUpdate) -> dict[str, object]:
        try:
            await qualify(request)
            snapshot = service.update(
                ark_api_key=(
                    request.ark_api_key.get_secret_value()
                    if request.ark_api_key is not None
                    else None
                ),
                multimodal_model_id=request.multimodal_model_id,
                image_model_id=request.image_model_id,
                pricing_overrides=request.pricing_overrides,
            )
            return snapshot.public_projection()
        except (ProviderSecretError, SettingsError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "model_runtime_invalid", "message": str(exc)},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail={"code": safe_connection_code(exc)}
            ) from exc

    @router.delete("/adapters/{adapter_id}/credential")
    async def delete_secret(adapter_id: str) -> dict[str, object]:
        try:
            return service.delete(adapter_id).public_projection()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="adapter not found") from exc
        except ProviderSecretError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/adapters/{adapter_id}/test")
    async def test_adapter(
        adapter_id: str, request: ModelRuntimeUpdate
    ) -> dict[str, object]:
        if adapter_id != ARK_ADAPTER_ID:
            raise HTTPException(status_code=404, detail="adapter not found")
        try:
            result = await qualify(request)
            return {"adapter_id": adapter_id, **result}
        except (SettingsError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "capability_mismatch", "message": str(exc)},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail={"code": safe_connection_code(exc)}
            ) from exc

    @router.api_route("/settings/providers", methods=["GET", "PUT"])
    async def deprecated_provider_settings() -> None:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "provider_settings_deprecated",
                "replacement": "/api/v1/settings/model-runtime",
            },
        )

    return router


__all__ = ["ModelRuntimeUpdate", "provider_settings_router"]
