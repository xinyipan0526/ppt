"""Versioned model runtime configuration with atomic Ark credential updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.settings import Settings, SettingsError, validate_settings
from ppt_agent.providers.capabilities import ModelCategory
from ppt_agent.providers.catalog import (
    ARK_ADAPTER_ID,
    DEFAULT_MODEL_CATALOG,
    ModelCatalog,
    ModelCatalogEntry,
)


SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class ModelRuntimeSnapshot:
    mode: str
    activation: str
    adapter_id: str
    adapter_status: dict[str, object]
    multimodal_model: ModelCatalogEntry | None
    image_model: ModelCatalogEntry | None
    configuration_version: int
    pricing_overrides: dict[str, object]
    legacy_configuration_detected: bool = False

    def public_projection(self) -> dict[str, object]:
        def model(entry: ModelCatalogEntry | None) -> dict[str, object] | None:
            if entry is None:
                return None
            return {
                "adapter_id": entry.adapter_id,
                "model_id": entry.model_id,
                "display_name": entry.display_name,
                "category": entry.category.value,
                "lifecycle": entry.lifecycle.value,
                "capabilities": [item.value for item in sorted(entry.capabilities)],
                "recommended": entry.recommended,
            }

        return {
            "mode": self.mode,
            "activation": self.activation,
            "adapter_id": self.adapter_id,
            "adapter_status": dict(self.adapter_status),
            "multimodal_model": model(self.multimodal_model),
            "image_model": model(self.image_model),
            "configuration_version": self.configuration_version,
            "pricing_overrides": dict(self.pricing_overrides),
            "legacy_configuration_detected": self.legacy_configuration_detected,
        }


ProviderConfigurationSnapshot = ModelRuntimeSnapshot


class ProviderConfigurationService:
    def __init__(
        self,
        settings: Settings,
        secrets: FileProviderSecretStore,
        *,
        is_idle: Callable[[], bool],
        activate: Callable[[Settings], None],
        qualify: Callable[[Settings], None] | None = None,
        settings_path: Path | None = None,
        catalog: ModelCatalog = DEFAULT_MODEL_CATALOG,
    ) -> None:
        self.secrets = secrets
        self._is_idle = is_idle
        self._activate = activate
        self._qualify = qualify or (lambda _: None)
        self._settings_path = settings_path
        self._catalog = catalog
        self._activation = "active"
        self._configuration_version = 1
        self._legacy_configuration_detected = False
        self._settings = self._load(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def snapshot(self) -> ModelRuntimeSnapshot:
        status = self.secrets.status(ARK_ADAPTER_ID)
        multimodal = self._catalog_entry(
            self._settings.multimodal_model_id, ModelCategory.MULTIMODAL
        )
        image = self._catalog_entry(
            self._settings.image_model_id, ModelCategory.IMAGE
        )
        return ModelRuntimeSnapshot(
            mode=self._settings.provider_mode,
            activation=self._activation,
            adapter_id=self._settings.adapter_id,
            adapter_status={
                "configured": status.configured or bool(self._settings.ark_api_key),
                "backend": (
                    status.backend if status.configured else "process_configuration"
                ),
            },
            multimodal_model=multimodal,
            image_model=image,
            configuration_version=self._configuration_version,
            pricing_overrides=dict(self._settings.pricing_overrides),
            legacy_configuration_detected=self._legacy_configuration_detected,
        )

    def update(
        self,
        *,
        ark_api_key: str | None,
        multimodal_model_id: str,
        image_model_id: str,
        pricing_overrides: dict[str, object] | None = None,
    ) -> ModelRuntimeSnapshot:
        self._catalog.require(multimodal_model_id, ModelCategory.MULTIMODAL)
        self._catalog.require(image_model_id, ModelCategory.IMAGE)
        self._validate_overrides(pricing_overrides or {})

        previous_secret = self.secrets.load(ARK_ADAPTER_ID)
        effective_secret = ark_api_key.strip() if ark_api_key is not None else previous_secret
        if not effective_secret:
            effective_secret = self._settings.ark_api_key
        candidate = replace(
            self._settings,
            provider_mode="cloud" if effective_secret else "deterministic",
            adapter_id=ARK_ADAPTER_ID,
            ark_api_key=effective_secret,
            multimodal_model_id=multimodal_model_id,
            image_model_id=image_model_id,
            pricing_overrides=dict(pricing_overrides or {}),
        )
        validate_settings(candidate)

        previous_document = self._read_persisted_bytes()
        replaced_secret = ark_api_key is not None
        try:
            if replaced_secret:
                self.secrets.replace(ARK_ADAPTER_ID, ark_api_key)
            self._qualify(candidate)
            self._persist(candidate)
        except Exception:
            if replaced_secret:
                if previous_secret is None:
                    self.secrets.delete(ARK_ADAPTER_ID)
                else:
                    self.secrets.replace(ARK_ADAPTER_ID, previous_secret)
            self._restore_persisted_bytes(previous_document)
            raise

        self._settings = candidate
        self._configuration_version += 1
        self._legacy_configuration_detected = False
        if self._is_idle():
            self._activate(candidate)
            self._activation = "active"
        else:
            self._activation = "pending"
        return self.snapshot()

    def delete(self, adapter_id: str = ARK_ADAPTER_ID) -> ModelRuntimeSnapshot:
        if adapter_id != ARK_ADAPTER_ID:
            raise ValueError("unsupported adapter")
        previous = self.secrets.load(ARK_ADAPTER_ID)
        self.secrets.delete(ARK_ADAPTER_ID)
        try:
            candidate = replace(
                self._settings, provider_mode="deterministic", ark_api_key=None
            )
            self._persist(candidate)
        except Exception:
            if previous is not None:
                self.secrets.replace(ARK_ADAPTER_ID, previous)
            raise
        self._settings = candidate
        self._configuration_version += 1
        if self._is_idle():
            self._activate(candidate)
            self._activation = "active"
        else:
            self._activation = "pending"
        return self.snapshot()

    def activate_if_idle(self) -> ModelRuntimeSnapshot:
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
            if loaded.get("schema_version") == SCHEMA_VERSION:
                values = {
                    name: loaded.get(name)
                    for name in (
                        "adapter_id",
                        "multimodal_model_id",
                        "image_model_id",
                        "pricing_overrides",
                    )
                }
                if values["pricing_overrides"] is None:
                    values["pricing_overrides"] = {}
            else:
                self._legacy_configuration_detected = True

        secret = self.secrets.load(ARK_ADAPTER_ID) or base.ark_api_key
        candidate = replace(base, **values, ark_api_key=secret)
        complete = bool(
            secret
            and candidate.adapter_id == ARK_ADAPTER_ID
            and candidate.multimodal_model_id
            and candidate.image_model_id
        )
        candidate = replace(
            candidate, provider_mode="cloud" if complete else "deterministic"
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
        document = {
            "schema_version": SCHEMA_VERSION,
            "adapter_id": settings.adapter_id,
            "multimodal_model_id": settings.multimodal_model_id,
            "image_model_id": settings.image_model_id,
            "pricing_overrides": dict(settings.pricing_overrides),
        }
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

    def _catalog_entry(
        self, model_id: str | None, category: ModelCategory
    ) -> ModelCatalogEntry | None:
        if model_id is None:
            return None
        try:
            return self._catalog.require(model_id, category)
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _validate_overrides(values: dict[str, object]) -> None:
        for name, value in values.items():
            if not isinstance(name, str) or not name:
                raise SettingsError("pricing override names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise SettingsError("pricing overrides must be non-negative numbers")

    def _read_persisted_bytes(self) -> bytes | None:
        if self._settings_path is None:
            return None
        try:
            return self._settings_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SettingsError("persisted provider settings cannot be read") from exc

    def _restore_persisted_bytes(self, value: bytes | None) -> None:
        if self._settings_path is None:
            return
        try:
            if value is None:
                self._settings_path.unlink(missing_ok=True)
            else:
                self._settings_path.write_bytes(value)
        except OSError as exc:
            raise SettingsError("provider settings rollback failed") from exc


__all__ = [
    "ModelRuntimeSnapshot",
    "ProviderConfigurationService",
    "ProviderConfigurationSnapshot",
    "SCHEMA_VERSION",
]
