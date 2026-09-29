"""Provider-independent image generation and editing protocol."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal, Protocol


ImageProviderErrorCode = Literal[
    "invalid_request",
    "authentication_error",
    "insufficient_balance",
    "rate_limited",
    "temporary_unavailable",
    "content_rejected",
    "unknown_remote_outcome",
    "invalid_provider_result",
]


@dataclass(frozen=True, slots=True)
class ImageInput:
    artifact_id: str
    media_type: str
    content: bytes
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.media_type or not self.content:
            raise ValueError("image identity, media type, and content are required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("image dimensions must be positive")
        if self.media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("unsupported input image media type")


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    model: str
    prompt: str
    width: int
    height: int
    user_id: str
    reference_images: tuple[ImageInput, ...] = ()
    edit_instruction: str | None = None
    output_format: Literal["png", "jpeg"] = "png"

    def __post_init__(self) -> None:
        if not self.model or not self.prompt.strip() or not self.user_id:
            raise ValueError("model, prompt, and user_id are required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("output dimensions must be positive")
        ids = [image.artifact_id for image in self.reference_images]
        if len(ids) != len(set(ids)):
            raise ValueError("reference image artifact ids must be unique")
        if self.edit_instruction is not None and not self.reference_images:
            raise ValueError("image editing requires a reference image")

    def fingerprint(self) -> str:
        payload = {
            "model": self.model,
            "prompt": self.prompt,
            "width": self.width,
            "height": self.height,
            "user_id": self.user_id,
            "references": [
                {
                    "artifact_id": item.artifact_id,
                    "media_type": item.media_type,
                    "content_hash": sha256(item.content).hexdigest(),
                    "width": item.width,
                    "height": item.height,
                }
                for item in self.reference_images
            ],
            "edit_instruction": self.edit_instruction,
            "output_format": self.output_format,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ImageUsage:
    generated_images: int
    output_tokens: int = 0
    input_images: int = 0

    def __post_init__(self) -> None:
        if min(self.generated_images, self.output_tokens, self.input_images) < 0:
            raise ValueError("image usage cannot be negative")


@dataclass(frozen=True, slots=True)
class ImageGenerationResult:
    response_id: str
    media_type: str
    width: int
    height: int
    usage: ImageUsage
    response_summary: dict[str, object]
    content: bytes | None = None
    result_url: str | None = None

    def __post_init__(self) -> None:
        if not self.response_id or self.width <= 0 or self.height <= 0:
            raise ValueError("result identity and positive dimensions are required")
        if (self.content is None) == (self.result_url is None):
            raise ValueError("exactly one of content or result_url is required")


@dataclass(frozen=True, slots=True)
class ImageProviderCallError(Exception):
    code: ImageProviderErrorCode
    retryable: bool
    billing_known: bool
    confirmed_uncharged: bool
    usage: ImageUsage | None
    safe_summary: dict[str, object]

    def __str__(self) -> str:
        return self.code


class ImageGenerationProvider(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...

