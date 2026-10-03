"""Volcengine Ark Seedream image generation adapter."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from ppt_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProviderCallError,
    ImageUsage,
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
        body: dict[str, Any] = {
            "model": request.model,
            "prompt": prompt,
            "size": f"{request.width}x{request.height}",
            "output_format": request.output_format,
            "response_format": "url",
            "watermark": False,
        }
        if request.reference_images:
            body["image"] = [
                "data:"
                + item.media_type
                + ";base64,"
                + base64.b64encode(item.content).decode("ascii")
                for item in request.reference_images
            ]
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
            raise ImageProviderCallError(
                code, retryable, known, known, None, {"http_status": response.status_code}
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
            size = str(item.get("size", f"{request.width}x{request.height}"))
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
