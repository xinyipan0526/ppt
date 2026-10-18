"""Volcengine Ark Seedream image generation adapter."""

from __future__ import annotations

import base64
import math
import re
from typing import Any

import httpx

from ppt_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProviderCallError,
    ImageUsage,
    sanitize_provider_diagnostic_identifier,
    sanitize_provider_error_message,
)


_ERRORS = {
    400: ("invalid_request", False),
    401: ("authentication_error", False),
    402: ("insufficient_balance", False),
    422: ("invalid_request", False),
    429: ("rate_limited", True),
    500: ("temporary_unavailable", True),
    503: ("temporary_unavailable", True),
}
_UNCHARGED = {400, 401, 402, 422}
_MIN_OUTPUT_PIXELS = 921_600
_REQUEST_ID_IN_MESSAGE = re.compile(
    r"\brequest[ _-]?id\s*:\s*([A-Za-z0-9][A-Za-z0-9._:-]{0,127})",
    re.IGNORECASE,
)


def _supported_output_size(width: int, height: int) -> tuple[int, int]:
    """Scale a layout box up to Seedream's minimum while preserving its ratio."""

    if width * height >= _MIN_OUTPUT_PIXELS:
        return width, height
    scale = math.sqrt(_MIN_OUTPUT_PIXELS / (width * height))
    output_width = math.ceil(width * scale)
    output_height = math.ceil(height * scale)
    return output_width, output_height


class SeedreamProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("Seedream API key is required")
        if not base_url.startswith("https://"):
            raise ValueError("Seedream base URL must use HTTPS")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        prompt = request.prompt
        if request.edit_instruction:
            prompt = f"{prompt}\nEdit instruction: {request.edit_instruction}"
        output_width, output_height = _supported_output_size(
            request.width, request.height
        )
        body: dict[str, Any] = {
            "model": request.model,
            "prompt": prompt,
            "size": f"{output_width}x{output_height}",
            "output_format": request.output_format,
            "response_format": "url",
            "watermark": False,
        }
        image_sensitive_values: tuple[str, ...] = ()
        if request.reference_images:
            encoded_images = [
                "data:"
                + item.media_type
                + ";base64,"
                + base64.b64encode(item.content).decode("ascii")
                for item in request.reference_images
            ]
            body["image"] = encoded_images
            image_sensitive_values = tuple(
                value
                for encoded in encoded_images
                for value in (encoded, encoded.partition(",")[2])
            )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                response = await client.post(
                    f"{self._base_url}/images/generations",
                    headers={"authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.RequestError as exc:
                raise ImageProviderCallError(
                    "unknown_remote_outcome", False, False, False, None,
                    {"exception_type": type(exc).__name__},
                ) from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= 400:
            code, retryable = _ERRORS.get(response.status_code, ("temporary_unavailable", False))
            known = response.status_code in _UNCHARGED
            summary: dict[str, object] = {
                "http_status": response.status_code,
                "provider_error_type": code,
            }
            try:
                error_payload = response.json()
            except (TypeError, ValueError):
                error_payload = None
            error = (
                error_payload.get("error")
                if isinstance(error_payload, dict)
                else None
            )
            provider_request_id = next(
                (
                    identifier
                    for candidate in (
                        response.headers.get("x-request-id"),
                        response.headers.get("x-tt-logid"),
                    )
                    if (
                        identifier := sanitize_provider_diagnostic_identifier(
                            candidate,
                            sensitive_values=(
                                self._api_key,
                                request.prompt,
                                request.edit_instruction,
                            ),
                            opaque_sensitive_values=image_sensitive_values,
                        )
                    )
                    is not None
                ),
                None,
            )
            if isinstance(error, dict):
                provider_error_code = sanitize_provider_diagnostic_identifier(
                    error.get("code"),
                    sensitive_values=(
                        self._api_key,
                        request.prompt,
                        request.edit_instruction,
                    ),
                    opaque_sensitive_values=image_sensitive_values,
                )
                if provider_error_code is not None:
                    summary["provider_error_code"] = provider_error_code
                provider_message = sanitize_provider_error_message(
                    error.get("message"),
                    sensitive_values=(
                        self._api_key,
                        request.prompt,
                        request.edit_instruction,
                    ),
                    opaque_sensitive_values=image_sensitive_values,
                )
                if provider_message is not None:
                    summary["provider_error_message"] = provider_message
                request_id_candidates = (
                    error.get("request_id"),
                    error_payload.get("request_id")
                    if isinstance(error_payload, dict)
                    else None,
                )
                provider_request_id = provider_request_id or next(
                    (
                        identifier
                        for candidate in request_id_candidates
                        if (
                            identifier := sanitize_provider_diagnostic_identifier(
                                candidate,
                                sensitive_values=(
                                    self._api_key,
                                    request.prompt,
                                    request.edit_instruction,
                                ),
                                opaque_sensitive_values=image_sensitive_values,
                            )
                        )
                        is not None
                    ),
                    None,
                )
                raw_provider_message = error.get("message")
                if provider_request_id is None and isinstance(
                    raw_provider_message, str
                ):
                    match = _REQUEST_ID_IN_MESSAGE.search(raw_provider_message)
                    if match is not None:
                        provider_request_id = sanitize_provider_diagnostic_identifier(
                            match.group(1),
                            sensitive_values=(
                                self._api_key,
                                request.prompt,
                                request.edit_instruction,
                            ),
                            opaque_sensitive_values=image_sensitive_values,
                        )
            if provider_request_id is not None:
                summary["provider_request_id"] = provider_request_id
            raise ImageProviderCallError(
                code, retryable, known, known, None, summary
            )
        try:
            payload = response.json()
            data = payload["data"]
            item = data[0]
            usage_value = payload["usage"]
            usage = ImageUsage(
                generated_images=int(usage_value["generated_images"]),
                output_tokens=int(usage_value.get("output_tokens", 0)),
                input_images=int(usage_value.get("input_images", 0)),
            )
            size = str(item.get("size", f"{output_width}x{output_height}"))
            width, height = (int(part) for part in size.lower().split("x", 1))
            url = item.get("url")
            encoded = item.get("b64_json")
            if bool(url) == bool(encoded):
                raise ValueError("exactly one image result representation is required")
            content = base64.b64decode(encoded, validate=True) if encoded else None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageProviderCallError(
                "invalid_provider_result", False, "usage" in locals(), False,
                locals().get("usage"), {"reason": "malformed_success"},
            ) from exc
        response_id = str(payload.get("id") or f"seedream-{payload.get('created', '')}")
        return ImageGenerationResult(
            response_id=response_id,
            media_type="image/jpeg" if request.output_format == "jpeg" else "image/png",
            width=width,
            height=height,
            usage=usage,
            response_summary={"response_id": response_id, "generated_images": usage.generated_images},
            content=content,
            result_url=str(url) if url else None,
        )
