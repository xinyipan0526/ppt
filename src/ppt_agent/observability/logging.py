"""Workspace-owned redacting JSONL logging."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_REDACTED = "[REDACTED]"
_MAX_STRING = 512
_MAX_DEPTH = 4
_MAX_ITEMS = 32
_STANDARD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|passwd|secret|credential|prompt|source[_-]?content|raw[_-]?(?:body|request|response))",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|digest)\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:prompt|source[_ -]?content)\s*[:=]\s*)[^\n]+"),
    re.compile(r"(?i)(?:(?:[A-Za-z]:)?[\\/][^\s,;]*?(?:\.secrets|secrets)[\\/]|\b(?:\.secrets|secrets)[\\/])[^\s,;]+"),
)


def _redact_text(value: str) -> str:
    for pattern in _SENSITIVE_TEXT:
        value = pattern.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    return value[:_MAX_STRING]


def _safe_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _SENSITIVE_KEY.search(key) or key.lower().endswith(("_path", "_file")) and ".secrets" in str(value):
        return _REDACTED
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            str(item_key)[:_MAX_STRING]: _safe_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in list(value.items())[:_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:_MAX_ITEMS]]
    return _redact_text(str(value))


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_FIELDS or key in payload or key.startswith("_"):
                continue
            payload[key] = _safe_value(value, key=key)
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_file_logging(
    workspace: Path,
    *,
    level: int = logging.INFO,
    max_bytes: int,
    backup_count: int,
) -> logging.Handler:
    """Attach a bounded, redacting JSONL handler to the process root logger."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if backup_count <= 0:
        raise ValueError("backup_count must be positive")
    logs = Path(workspace) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs / "ppt-agent.jsonl",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(_JSONFormatter())
    logging.getLogger().addHandler(handler)
    return handler


def shutdown_file_logging(handler: logging.Handler) -> None:
    """Detach and close a handler previously returned by configure_file_logging."""
    root = logging.getLogger()
    root.removeHandler(handler)
    handler.flush()
    handler.close()
