"""Provider-independent structured text request and result protocol."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class StructuredTextRequest:
    model: str
    schema_name: str
    schema_version: int
    json_schema: dict[str, Any]
    system_prompt: str
    user_content: str
    max_output_tokens: int
    user_id: str

    def __post_init__(self) -> None:
        if not self.model or not self.schema_name or self.schema_version < 1:
            raise ValueError("model and versioned schema identity are required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if not self.user_id:
            raise ValueError("user_id must be non-empty")


@dataclass(frozen=True, slots=True)
class TextUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        if min(
            self.input_tokens,
            self.output_tokens,
            self.cached_tokens,
            self.reasoning_tokens,
        ) < 0:
            raise ValueError("token usage cannot be negative")


@dataclass(frozen=True, slots=True)
class StructuredTextResult:
    response_id: str
    value: dict[str, Any]
    usage: TextUsage
    response_summary: dict[str, Any]


ProviderErrorCode = Literal[
    "invalid_request",
    "authentication_error",
    "insufficient_balance",
    "rate_limited",
    "temporary_unavailable",
    "content_rejected",
    "unknown_remote_outcome",
]


@dataclass(slots=True)
class ProviderCallError(Exception):
    code: ProviderErrorCode
    retryable: bool
    billing_known: bool
    confirmed_uncharged: bool
    usage: TextUsage | None
    safe_summary: dict[str, Any]

    def __str__(self) -> str:
        return self.code


class StructuredTextProvider(Protocol):
    async def complete(self, request: StructuredTextRequest) -> StructuredTextResult: ...
