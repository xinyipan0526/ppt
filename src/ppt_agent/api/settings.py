"""Redacted browser configuration endpoints for cloud providers."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ppt_agent.api.security import LocalHTTPSecurity
from ppt_agent.config.provider_secrets import PROVIDERS, ProviderSecretError
from ppt_agent.config.settings import SettingsError
from ppt_agent.runtime.provider_config import ProviderConfigurationService


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deepseek_api_key: SecretStr | None = None
    seedream_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None
    seedream_result_hosts: tuple[str, ...] = ()
    pricing_version: str = Field(min_length=1, max_length=100)
    deepseek_input_per_million: int = Field(gt=0)
    deepseek_output_per_million: int = Field(gt=0)
    seedream_per_image: int = Field(gt=0)
    qwen_input_per_million: int = Field(gt=0)
    qwen_output_per_million: int = Field(gt=0)


def provider_settings_router(
    *, service: ProviderConfigurationService, security: LocalHTTPSecurity
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/settings/providers",
        dependencies=[Depends(security.authenticate)],
    )

    @router.get("")
    async def get_settings() -> dict:
        return asdict(service.activate_if_idle())

    @router.put("")
    async def update_settings(request: ProviderSettingsUpdate) -> dict:
        try:
            secrets = {
                provider: secret.get_secret_value()
                for provider in PROVIDERS
                if (secret := getattr(request, f"{provider}_api_key")) is not None
            }
            changes = request.model_dump(
                exclude={f"{provider}_api_key" for provider in PROVIDERS}
            )
            return asdict(service.update(secret_values=secrets, **changes))
        except (ProviderSecretError, SettingsError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "provider_configuration_invalid", "message": str(exc)},
            ) from exc

    @router.delete("/{provider}")
    async def delete_secret(provider: str) -> dict:
        if provider not in PROVIDERS:
            raise HTTPException(status_code=404, detail="provider not found")
        try:
            return asdict(service.delete(provider))
        except ProviderSecretError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
