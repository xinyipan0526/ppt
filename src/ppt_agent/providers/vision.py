"""Provider-independent vision-understanding and OCR protocol."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal, Protocol

from ppt_agent.domain.vision import OCRReportV1


VisionProviderErrorCode = Literal[
    "invalid_request",
    "authentication_error",
    "insufficient_balance",
    "rate_limited",
    "temporary_unavailable",
    "content_rejected",
    "unknown_remote_outcome",
]


@dataclass(frozen=True, slots=True)
class VisionRequest:
    model: str
    image: bytes
    media_type: str
    prompt: str
    schema_name: str
    schema_version: int
    json_schema: dict[str, Any]
    max_output_tokens: int
    user_id: str

    def __post_init__(self) -> None:
        if not self.model or not self.image or not self.prompt.strip() or not self.user_id:
            raise ValueError("model, image, prompt, and user_id are required")
        if not self.schema_name or self.schema_version < 1:
            raise ValueError("versioned schema identity is required")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("unsupported vision image media type")

    def fingerprint(self) -> str:
        metadata = {
            "model": self.model,
            "image_hash": sha256(self.image).hexdigest(),
            "media_type": self.media_type,
            "prompt": self.prompt,
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "schema": self.json_schema,
            "max_output_tokens": self.max_output_tokens,
            "user_id": self.user_id,
        }
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class OCRRequest:
    model: str
    image: bytes
    media_type: str
    max_output_tokens: int
    user_id: str
    expected_text: str | None = None

    def __post_init__(self) -> None:
        if not self.model or not self.image or not self.user_id:
            raise ValueError("model, image, and user_id are required")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("unsupported OCR image media type")

    def fingerprint(self) -> str:
        metadata = {
            "model": self.model,
            "image_hash": sha256(self.image).hexdigest(),
            "media_type": self.media_type,
            "max_output_tokens": self.max_output_tokens,
            "user_id": self.user_id,
            "expected_text": self.expected_text,
        }
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class VisionUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("vision usage cannot be negative")


@dataclass(frozen=True, slots=True)
class VisionResult:
    response_id: str
    value: dict[str, Any]
    usage: VisionUsage
    response_summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class OCRResult:
    response_id: str
    report: OCRReportV1
    usage: VisionUsage
    response_summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class VisionProviderCallError(Exception):
    code: VisionProviderErrorCode
    retryable: bool
    billing_known: bool
    confirmed_uncharged: bool
    usage: VisionUsage | None
    safe_summary: dict[str, object]

    def __str__(self) -> str:
        return self.code


class VisionUnderstandingProvider(Protocol):
    async def analyze(self, request: VisionRequest) -> VisionResult: ...


class OCRProvider(Protocol):
    async def recognize(self, request: OCRRequest) -> OCRResult: ...

