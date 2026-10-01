"""Runtime provider configuration assembled from non-secret settings and secrets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppt_agent.config.provider_secrets import (
    FileProviderSecretStore,
    PROVIDERS,
    ProviderSecretError,
)
from ppt_agent.config.settings import Settings, SettingsError, validate_settings


@dataclass(frozen=True, slots=True)
class ProviderConfigurationSnapshot:
    mode: str
    activation: str
    providers: dict[str, dict[str, object]]
    pricing_version: str


class ProviderConfigurationService:
    def __init__(
        self,
        settings: Settings,
        secrets: FileProviderSecretStore,
        *,
        is_idle: Callable[[], bool],
        activate: Callable[[Settings], None],
        settings_path: Path | None = None,
    ) -> None:
        self.secrets = secrets
        self._is_idle = is_idle
        self._activate = activate
        self._settings_path = settings_path
        self._settings = self._load(settings)
        self._activation = "active"

    @property
    def settings(self) -> Settings:
        return self._settings

    def snapshot(self) -> ProviderConfigurationSnapshot:
        statuses = {name: self.secrets.status(name) for name in PROVIDERS}
        return ProviderConfigurationSnapshot(
            mode=self._settings.provider_mode,
            activation=self._activation,
            providers={
                name: {
                    "configured": status.configured or bool(
                        getattr(self._settings, f"{name}_api_key")
                    ),
                    "backend": (
                        status.backend if status.configured else "process_configuration"
                    ),
                    "masked_suffix": status.masked_suffix,
                }
                for name, status in sorted(statuses.items())
            },
            pricing_version=self._settings.pricing_version,
        )

    def update(
        self,
        *,
        secret_values: dict[str, str] | None = None,
        **changes: Any,
    ) -> ProviderConfigurationSnapshot:
        replacements = secret_values or {}
        if set(replacements).difference(PROVIDERS):
            raise ProviderSecretError("unsupported provider secret")
        previous = {name: self.secrets.load(name) for name in replacements}
        try:
            for name, value in replacements.items():
                self.secrets.replace(name, value)
            keys = {
                name: self.secrets.load(name)
                or getattr(self._settings, f"{name}_api_key")
                for name in PROVIDERS
            }
            complete = all(keys.values())
            candidate = replace(
                self._settings,
                **changes,
                provider_mode="cloud" if complete else "deterministic",
                deepseek_api_key=keys["deepseek"],
                seedream_api_key=keys["seedream"],
                qwen_api_key=keys["qwen"],
            )
            validate_settings(candidate)
            self._persist(candidate)
        except Exception:
            for name, value in previous.items():
                if value is None:
                    self.secrets.delete(name)
                else:
                    self.secrets.replace(name, value)
            raise
        self._settings = candidate
        if self._is_idle():
            self._activate(candidate)
            self._activation = "active"
        else:
            self._activation = "pending"
        return self.snapshot()

    def delete(self, provider: str) -> ProviderConfigurationSnapshot:
        previous = self.secrets.load(provider)
        self.secrets.delete(provider)
        try:
            return self.update()
        except Exception:
            if previous is not None:
                self.secrets.replace(provider, previous)
            raise

    def activate_if_idle(self) -> ProviderConfigurationSnapshot:
        if self._activation == "pending" and self._is_idle():
            self._activate(self._settings)
            self._activation = "active"
        return self.snapshot()

    def _load(self, base: Settings) -> Settings:
        values: dict[str, Any] = {}
        if self._settings_path is not None and self._settings_path.exists():
            try:
                loaded = json.loads(self._settings_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SettingsError("persisted provider settings cannot be read") from exc
            if not isinstance(loaded, dict):
                raise SettingsError("persisted provider settings must be an object")
            values = {name: loaded[name] for name in _PERSISTED_FIELDS if name in loaded}
            if "seedream_result_hosts" in values:
                values["seedream_result_hosts"] = tuple(values["seedream_result_hosts"])
        keys = {
            name: self.secrets.load(name) or getattr(base, f"{name}_api_key")
            for name in PROVIDERS
        }
        complete = all(keys.values())
        candidate = replace(
            base,
            **values,
            provider_mode=(
                "cloud"
                if complete and (values or base.provider_mode == "cloud")
                else "deterministic"
            ),
            deepseek_api_key=keys["deepseek"],
            seedream_api_key=keys["seedream"],
            qwen_api_key=keys["qwen"],
        )
        validate_settings(candidate)
        return candidate

    def _persist(self, settings: Settings) -> None:
        if self._settings_path is None:
            return
        path = self._settings_path
        if path.is_symlink():
            raise SettingsError("provider settings path cannot be a symbolic link")
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {name: getattr(settings, name) for name in _PERSISTED_FIELDS}
        document["seedream_result_hosts"] = list(settings.seedream_result_hosts)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise SettingsError("provider settings cannot be stored") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


_PERSISTED_FIELDS = (
    "deepseek_base_url", "seedream_base_url", "qwen_base_url",
    "deepseek_model", "seedream_model", "qwen_vision_model", "qwen_ocr_model",
    "seedream_result_hosts", "pricing_currency", "pricing_version",
    "deepseek_input_per_million", "deepseek_output_per_million",
    "deepseek_cached_input_per_million", "seedream_per_image",
    "qwen_input_per_million", "qwen_output_per_million",
)
