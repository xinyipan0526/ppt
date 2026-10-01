"""Configuration loading with CLI > environment > TOML > defaults precedence."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping
from urllib.parse import urlsplit


class SettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    workspace: Path = Path("./workspace")
    host: str = "127.0.0.1"
    port: int = 8000
    production: bool = False
    provider_mode: str = "deterministic"
    deepseek_api_key: str | None = None
    seedream_api_key: str | None = None
    qwen_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    seedream_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_model: str = "deepseek-chat"
    seedream_model: str = "doubao-seedream-4-0-250828"
    qwen_vision_model: str = "qwen-vl-max"
    qwen_ocr_model: str = "qwen-vl-ocr-latest"
    seedream_result_hosts: tuple[str, ...] = ()
    pricing_currency: str = "CNY"
    pricing_version: str = "deterministic-v1"
    deepseek_input_per_million: int = 0
    deepseek_output_per_million: int = 0
    deepseek_cached_input_per_million: int = 0
    seedream_per_image: int = 0
    qwen_input_per_million: int = 0
    qwen_output_per_million: int = 0
    local_access_token: str | None = None
    local_access_token_file: Path | None = None
    chromium_executable: Path | None = None
    font_roots: tuple[Path, ...] = (
        Path("C:/Windows/Fonts"), Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    )
    global_concurrency: int = 4
    project_concurrency: int = 2

    def redacted(self) -> dict[str, Any]:
        def secret(value: str | None) -> str | None:
            return "***configured***" if value else None

        return {
            "workspace": str(self.workspace),
            "server": {"host": self.host, "port": self.port, "production": self.production},
            "providers": {
                "mode": self.provider_mode,
                "deepseek_api_key": secret(self.deepseek_api_key),
                "seedream_api_key": secret(self.seedream_api_key),
                "qwen_api_key": secret(self.qwen_api_key),
                "deepseek_base_url": self.deepseek_base_url,
                "seedream_base_url": self.seedream_base_url,
                "qwen_base_url": self.qwen_base_url,
                "deepseek_model": self.deepseek_model,
                "seedream_model": self.seedream_model,
                "seedream_result_hosts": list(self.seedream_result_hosts),
                "qwen_vision_model": self.qwen_vision_model,
                "qwen_ocr_model": self.qwen_ocr_model,
                "pricing": {
                    "currency": self.pricing_currency,
                    "version": self.pricing_version,
                    "deepseek_input_per_million": self.deepseek_input_per_million,
                    "deepseek_output_per_million": self.deepseek_output_per_million,
                    "deepseek_cached_input_per_million": self.deepseek_cached_input_per_million,
                    "seedream_per_image": self.seedream_per_image,
                    "qwen_input_per_million": self.qwen_input_per_million,
                    "qwen_output_per_million": self.qwen_output_per_million,
                },
            },
            "authentication": {
                "local_access_token": secret(self.local_access_token),
                "local_access_token_file": str(self.local_access_token_file) if self.local_access_token_file else None,
            },
            "rendering": {
                "chromium_executable": str(self.chromium_executable) if self.chromium_executable else None,
                "font_roots": [str(item) for item in self.font_roots],
            },
        }


_ENV_MAP = {
    "PPT_AGENT_WORKSPACE": ("workspace", Path),
    "PPT_AGENT_HOST": ("host", str),
    "PPT_AGENT_PORT": ("port", int),
    "PPT_AGENT_PRODUCTION": ("production", lambda value: value.casefold() in {"1", "true", "yes", "on"}),
    "PPT_AGENT_PROVIDER_MODE": ("provider_mode", str),
    "DEEPSEEK_API_KEY": ("deepseek_api_key", str),
    "SEEDREAM_API_KEY": ("seedream_api_key", str),
    "QWEN_API_KEY": ("qwen_api_key", str),
    "PPT_AGENT_DEEPSEEK_BASE_URL": ("deepseek_base_url", str),
    "PPT_AGENT_SEEDREAM_BASE_URL": ("seedream_base_url", str),
    "PPT_AGENT_QWEN_BASE_URL": ("qwen_base_url", str),
    "PPT_AGENT_DEEPSEEK_MODEL": ("deepseek_model", str),
    "PPT_AGENT_SEEDREAM_MODEL": ("seedream_model", str),
    "PPT_AGENT_QWEN_VISION_MODEL": ("qwen_vision_model", str),
    "PPT_AGENT_QWEN_OCR_MODEL": ("qwen_ocr_model", str),
    "PPT_AGENT_SEEDREAM_RESULT_HOSTS": ("seedream_result_hosts", lambda value: tuple(item.strip().casefold() for item in value.split(",") if item.strip())),
    "PPT_AGENT_PRICING_CURRENCY": ("pricing_currency", str),
    "PPT_AGENT_PRICING_VERSION": ("pricing_version", str),
    "PPT_AGENT_DEEPSEEK_INPUT_PER_MILLION": ("deepseek_input_per_million", int),
    "PPT_AGENT_DEEPSEEK_OUTPUT_PER_MILLION": ("deepseek_output_per_million", int),
    "PPT_AGENT_DEEPSEEK_CACHED_INPUT_PER_MILLION": ("deepseek_cached_input_per_million", int),
    "PPT_AGENT_SEEDREAM_PER_IMAGE": ("seedream_per_image", int),
    "PPT_AGENT_QWEN_INPUT_PER_MILLION": ("qwen_input_per_million", int),
    "PPT_AGENT_QWEN_OUTPUT_PER_MILLION": ("qwen_output_per_million", int),
    "PPT_AGENT_ACCESS_TOKEN": ("local_access_token", str),
    "PPT_AGENT_ACCESS_TOKEN_FILE": ("local_access_token_file", Path),
    "PPT_AGENT_CHROMIUM_EXECUTABLE": ("chromium_executable", Path),
    "PPT_AGENT_FONT_ROOTS": ("font_roots", lambda value: tuple(Path(item.strip()) for item in value.split(os.pathsep) if item.strip())),
    "PPT_AGENT_GLOBAL_CONCURRENCY": ("global_concurrency", int),
    "PPT_AGENT_PROJECT_CONCURRENCY": ("project_concurrency", int),
}

_SECRET_FILE_ENV_MAP = {
    "DEEPSEEK_API_KEY_FILE": "deepseek_api_key",
    "SEEDREAM_API_KEY_FILE": "seedream_api_key",
    "QWEN_API_KEY_FILE": "qwen_api_key",
}


def load_settings(
    config_path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    values: dict[str, Any] = {}
    if config_path is not None:
        try:
            with config_path.open("rb") as stream:
                document = tomllib.load(stream)
        except OSError as exc:
            raise SettingsError(f"configuration file cannot be read: {config_path}") from exc
        values.update(_toml_values(document))
    environment = environ if environ is not None else os.environ
    for name, raw in environment.items():
        mapping = _ENV_MAP.get(name)
        if mapping is not None and raw != "":
            field, convert = mapping
            try:
                values[field] = convert(raw)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"invalid value for {name}") from exc
    for name, field in _SECRET_FILE_ENV_MAP.items():
        raw_path = environment.get(name)
        if not raw_path or field in values:
            continue
        try:
            secret = Path(raw_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SettingsError(f"secret file configured by {name} cannot be read") from exc
        if not secret or len(secret.encode("utf-8")) > 64 * 1024:
            raise SettingsError(f"secret file configured by {name} is empty or too large")
        values[field] = secret
    if overrides:
        values.update({key: value for key, value in overrides.items() if value is not None})
    settings = replace(Settings(), **values)
    _validate(settings)
    return settings


def _toml_values(document: Mapping[str, Any]) -> dict[str, Any]:
    server = document.get("server", {})
    providers = document.get("providers", {})
    auth = document.get("authentication", {})
    runtime = document.get("runtime", {})
    values = {
        "workspace": document.get("workspace"),
        "host": server.get("host"), "port": server.get("port"),
        "production": server.get("production"),
        "provider_mode": providers.get("mode"),
        "deepseek_base_url": providers.get("deepseek_base_url"),
        "seedream_base_url": providers.get("seedream_base_url"),
        "qwen_base_url": providers.get("qwen_base_url"),
        "deepseek_model": providers.get("deepseek_model"),
        "seedream_model": providers.get("seedream_model"),
        "qwen_vision_model": providers.get("qwen_vision_model"),
        "qwen_ocr_model": providers.get("qwen_ocr_model"),
        "seedream_result_hosts": providers.get("seedream_result_hosts"),
        "pricing_currency": providers.get("pricing_currency"),
        "pricing_version": providers.get("pricing_version"),
        "deepseek_input_per_million": providers.get("deepseek_input_per_million"),
        "deepseek_output_per_million": providers.get("deepseek_output_per_million"),
        "deepseek_cached_input_per_million": providers.get("deepseek_cached_input_per_million"),
        "seedream_per_image": providers.get("seedream_per_image"),
        "qwen_input_per_million": providers.get("qwen_input_per_million"),
        "qwen_output_per_million": providers.get("qwen_output_per_million"),
        "local_access_token_file": auth.get("token_file"),
        "chromium_executable": runtime.get("chromium_executable"),
        "font_roots": runtime.get("font_roots"),
        "global_concurrency": runtime.get("global_concurrency"),
        "project_concurrency": runtime.get("project_concurrency"),
    }
    path_fields = {"workspace", "local_access_token_file", "chromium_executable"}
    return {
        key: (
            Path(value) if key in path_fields
            else tuple(Path(item) for item in value) if key == "font_roots"
            else tuple(value) if key == "seedream_result_hosts" else value
        )
        for key, value in values.items() if value is not None
    }


def _validate(settings: Settings) -> None:
    if settings.provider_mode not in {"deterministic", "cloud"}:
        raise SettingsError("provider mode must be deterministic or cloud")
    if not 1 <= settings.port <= 65535:
        raise SettingsError("server port must be between 1 and 65535")
    if min(settings.global_concurrency, settings.project_concurrency) < 1:
        raise SettingsError("concurrency limits must be positive")
    if not settings.font_roots:
        raise SettingsError("at least one approved font root is required")
    if len(settings.pricing_currency) != 3 or not settings.pricing_currency.isupper():
        raise SettingsError("pricing currency must be an ISO-style three-letter uppercase code")
    prices = (
        settings.deepseek_input_per_million,
        settings.deepseek_output_per_million,
        settings.deepseek_cached_input_per_million,
        settings.seedream_per_image,
        settings.qwen_input_per_million,
        settings.qwen_output_per_million,
    )
    if min(prices) < 0:
        raise SettingsError("provider prices cannot be negative")
    if settings.provider_mode == "cloud":
        missing = [name for name, value in (
            ("DEEPSEEK_API_KEY", settings.deepseek_api_key),
            ("SEEDREAM_API_KEY", settings.seedream_api_key),
            ("QWEN_API_KEY", settings.qwen_api_key),
        ) if not value]
        if missing:
            raise SettingsError(f"cloud provider keys are required: {', '.join(missing)}")
        for name, endpoint in (
            ("DeepSeek", settings.deepseek_base_url),
            ("Seedream", settings.seedream_base_url),
            ("Qwen", settings.qwen_base_url),
        ):
            if urlsplit(endpoint).scheme != "https":
                raise SettingsError(f"{name} endpoint must use HTTPS in cloud mode")
        if not settings.seedream_result_hosts:
            raise SettingsError("Seedream result URL host allowlist is required in cloud mode")
        required_prices = {
            "deepseek_input_per_million": settings.deepseek_input_per_million,
            "deepseek_output_per_million": settings.deepseek_output_per_million,
            "seedream_per_image": settings.seedream_per_image,
            "qwen_input_per_million": settings.qwen_input_per_million,
            "qwen_output_per_million": settings.qwen_output_per_million,
        }
        missing_prices = [name for name, value in required_prices.items() if value <= 0]
        if missing_prices:
            raise SettingsError(
                "positive cloud pricing is required: " + ", ".join(missing_prices)
            )
        if not settings.pricing_version.strip():
            raise SettingsError("pricing version is required in cloud mode")
