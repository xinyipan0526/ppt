"""Alibaba Cloud Model Studio Qwen vision and OCR adapters."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from pydantic import ValidationError

from ppt_agent.domain.vision import NormalizedBoxV1, OCRReportV1, OCRSpanV1
from ppt_agent.providers.vision import (
    OCRRequest,
    OCRResult,
    VisionProviderCallError,
    VisionRequest,
    VisionResult,
    VisionUsage,
)


_ERRORS = {
    400: ("invalid_request", False), 401: ("authentication_error", False),
    402: ("insufficient_balance", False), 422: ("invalid_request", False),
    429: ("rate_limited", True), 500: ("temporary_unavailable", True),
    503: ("temporary_unavailable", True),
}
_UNCHARGED = {400, 401, 402, 422}


class _QwenBase:
    def __init__(self, api_key: str, base_url: str, *, client: httpx.AsyncClient | None = None, timeout_seconds: float = 90.0) -> None:
        if not api_key:
            raise ValueError("Qwen API key is required")
        if not base_url.startswith("https://"):
            raise ValueError("Qwen base URL must use HTTPS")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.RequestError as exc:
                raise VisionProviderCallError(
                    "unknown_remote_outcome", False, False, False, None,
                    {"exception_type": type(exc).__name__},
                ) from exc
        finally:
            if owns:
                await client.aclose()
        if response.status_code >= 400:
            code, retryable = _ERRORS.get(response.status_code, ("temporary_unavailable", False))
            known = response.status_code in _UNCHARGED
            raise VisionProviderCallError(
                code, retryable, known, known, None, {"http_status": response.status_code}
            )
        try:
            payload = response.json()
            usage_data = payload["usage"]
            payload["_parsed_usage"] = VisionUsage(
                input_tokens=int(usage_data["prompt_tokens"]),
                output_tokens=int(usage_data["completion_tokens"]),
            )
            return payload
        except (KeyError, TypeError, ValueError) as exc:
            raise VisionProviderCallError(
                "content_rejected", False, False, False, None,
                {"reason": "malformed_success"},
            ) from exc

    @staticmethod
    def _image(media_type: str, content: bytes) -> str:
        return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"

    @staticmethod
    def _content(payload: dict[str, Any]) -> tuple[str, VisionUsage, str]:
        try:
            text = payload["choices"][0]["message"]["content"]
            usage = payload["_parsed_usage"]
            response_id = str(payload["id"])
            if not isinstance(text, str):
                raise TypeError
            return text, usage, response_id
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionProviderCallError(
                "content_rejected", False, True, False,
                payload.get("_parsed_usage"), {"reason": "missing_content"},
            ) from exc


class QwenVisionProvider(_QwenBase):
    async def analyze(self, request: VisionRequest) -> VisionResult:
        body = {
            "model": request.model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": self._image(request.media_type, request.image)}},
                {"type": "text", "text": request.prompt},
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": f"{request.schema_name}_v{request.schema_version}",
                "strict": True, "schema": request.json_schema,
            }},
            "max_tokens": request.max_output_tokens,
            "user": request.user_id,
        }
        payload = await self._post(body)
        text, usage, response_id = self._content(payload)
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise TypeError
        except (json.JSONDecodeError, TypeError) as exc:
            raise VisionProviderCallError(
                "content_rejected", False, True, False, usage,
                {"reason": "malformed_structured_output"},
            ) from exc
        return VisionResult(response_id, value, usage, {"response_id": response_id})


class QwenOCRProvider(_QwenBase):
    async def recognize(self, request: OCRRequest) -> OCRResult:
        prompt = "Extract all text from the image. Return only the recognized text without commentary."
        body = {
            "model": request.model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": self._image(request.media_type, request.image)}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": request.max_output_tokens,
            "user": request.user_id,
        }
        payload = await self._post(body)
        text, usage, response_id = self._content(payload)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            report = OCRReportV1(full_text=text.strip(), spans=())
        else:
            try:
                if not isinstance(value, dict):
                    raise TypeError
                spans = []
                for item in value.get("spans", []):
                    x1, y1, x2, y2 = (float(part) / 1000 for part in item["box"])
                    spans.append(OCRSpanV1(
                        text=item["text"],
                        box=NormalizedBoxV1(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
                        confidence=item.get("confidence"),
                    ))
                report = OCRReportV1(full_text=value.get("full_text", ""), spans=tuple(spans))
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                raise VisionProviderCallError(
                    "content_rejected", False, True, False, usage,
                    {"reason": "malformed_ocr_output"},
                ) from exc
        return OCRResult(response_id, report, usage, {"response_id": response_id})
