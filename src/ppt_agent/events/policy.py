"""Mandatory safety policy for every persisted workflow event."""

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any


ABSOLUTE_MAX_PAYLOAD_BYTES = 65_536
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "file_content",
    "filecontent",
    "prompt",
    "raw_prompt",
    "rawprompt",
    "raw_response",
    "rawresponse",
    "response",
    "secret",
    "token",
}


class EventPayloadRejected(ValueError):
    """Raised when an event payload violates projection privacy limits."""


def validate_event_type(event_type: str) -> None:
    """Reject values that cannot safely occupy one SSE ``event`` line."""

    if len(event_type) > 150 or _EVENT_TYPE.fullmatch(event_type) is None:
        raise ValueError(
            "event_type must be lowercase dot-separated identifiers"
        )


def validate_event_payload(
    payload: dict[str, Any], *, max_payload_bytes: int = ABSOLUTE_MAX_PAYLOAD_BYTES
) -> None:
    """Enforce JSON, size, and sensitive-field rules at persistence boundary."""

    effective_limit = min(max_payload_bytes, ABSOLUTE_MAX_PAYLOAD_BYTES)
    if effective_limit <= 0:
        raise ValueError("max_payload_bytes must be positive")

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized = re.sub(
                    r"[^a-z0-9]+", "_", str(key).strip().lower()
                ).strip("_")
                compact = normalized.replace("_", "")
                if normalized in _SENSITIVE_KEYS or compact in _SENSITIVE_KEYS:
                    raise EventPayloadRejected(
                        f"event payload contains forbidden field: {key}"
                    )
                walk(nested)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for nested in value:
                walk(nested)

    walk(payload)
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EventPayloadRejected("event payload must be JSON serializable") from exc
    if len(encoded) > effective_limit:
        raise EventPayloadRejected("event payload exceeds size limit")

