"""Workspace-owned redacting JSONL logging."""

from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_REDACTED = "[REDACTED]"
_MAX_STRING = 512
_MAX_EXCEPTION = 2_048
_MAX_EXCEPTION_CHAIN = 4
_MAX_EXCEPTION_FRAMES = 2
_MAX_DEPTH = 4
_MAX_ITEMS = 32
_STANDARD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|passwd|secret|credential|prompt|source[_-]?content|raw[_-]?(?:body|request|response))",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = (
    re.compile(r"(?i)(authorization\s*:\s*[-!#$%&'*+.^_`|~0-9A-Za-z]+\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:prompt|source[_ -]?content)\s*[:=]\s*)[^\n]+"),
    re.compile(r"(?i)(?:(?:[A-Za-z]:)?[\\/][^\s,;]*?(?:\.secrets|secrets)[\\/]|\b(?:\.secrets|secrets)[\\/])[^\s,;]+"),
    re.compile(r"(?i)\b[\w-]*(?:secret|token|api[_-]?key|password|credential)[\w-]*\b"),
    re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|/)[^\s,;]+"),
)


def _redact_text(value: str, *, max_length: int = _MAX_STRING) -> str:
    for pattern in _SENSITIVE_TEXT:
        value = pattern.sub(
            lambda match: f"{match.group(1)}{_REDACTED}" if match.lastindex else _REDACTED,
            value,
        )
    return value[:max_length]


def _safe_exception(exc_info: tuple[type[BaseException], BaseException, object]) -> str:
    """Render a bounded traceback summary without exception messages or paths."""
    exception = exc_info[1]
    chain: list[BaseException] = []
    seen: set[int] = set()
    while exception is not None and id(exception) not in seen:
        seen.add(id(exception))
        chain.append(exception)
        if len(chain) >= _MAX_EXCEPTION_CHAIN:
            break
        if exception.__cause__ is not None:
            exception = exception.__cause__
        elif exception.__context__ is not None and not exception.__suppress_context__:
            exception = exception.__context__
        else:
            exception = None

    sections: list[str] = []
    for item in chain:
        exception_type = _redact_text(type(item).__name__, max_length=96)
        lines = [f"{exception_type}: {_REDACTED}"]
        frames = traceback.extract_tb(item.__traceback__)[-_MAX_EXCEPTION_FRAMES:]
        for frame in frames:
            # Basenames and callable names are sufficient to identify endpoint/user
            # code while absolute Workspace/user paths and source lines stay private.
            filename = _redact_text(Path(frame.filename).name, max_length=96)
            function = _redact_text(frame.name, max_length=128)
            lines.append(f"  at {filename}:{frame.lineno} in {function}")
        sections.append("\n".join(lines))
    summary = "Traceback (safe summary):\n" + "\ncaused by:\n".join(sections)
    return _redact_text(summary, max_length=_MAX_EXCEPTION)


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
            payload["exception"] = _safe_exception(record.exc_info)
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
    log_path = (logs / "ppt-agent.jsonl").resolve()
    root = logging.getLogger()
    # CLI commands can be invoked repeatedly inside one interpreter (notably
    # by embedding callers and tests).  Never leave one of our closed file
    # handlers on the root logger for a later command to write through.
    for previous_handler in tuple(root.handlers):
        stream = getattr(previous_handler, "stream", None)
        # Embedded CLI callers and pytest replace/close stderr between
        # invocations.  Do not leave any handler that would write through a
        # closed stream; doing so emits a logging-internal traceback on the
        # next command and can obscure the stable CLI error line.
        if stream is not None and getattr(stream, "closed", False):
            root.removeHandler(previous_handler)
            previous_handler.close()
    for previous_handler in root.handlers:
        if not getattr(previous_handler, "_ppt_agent_file_log", False):
            continue
        filename = getattr(previous_handler, "baseFilename", None)
        if filename is None or Path(filename).resolve() != log_path:
            continue
        previous_handler.setLevel(level)
        previous_handler.setFormatter(_JSONFormatter())
        if isinstance(previous_handler, RotatingFileHandler):
            previous_handler.maxBytes = max_bytes
            previous_handler.backupCount = backup_count
        return previous_handler
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(_JSONFormatter())
    setattr(handler, "_ppt_agent_file_log", True)
    audit_logger = logging.getLogger("ppt_agent")
    setattr(handler, "_previous_audit_logger_level", audit_logger.level)
    managed_loggers = [audit_logger]
    managed_loggers.extend(
        logger
        for name, logger in logging.Logger.manager.loggerDict.items()
        if name.startswith("ppt_agent.") and isinstance(logger, logging.Logger)
    )
    previous_disabled = {logger.name: logger.disabled for logger in managed_loggers}
    setattr(handler, "_previous_disabled_loggers", previous_disabled)
    for logger in managed_loggers:
        logger.disabled = False
    previous_level = audit_logger.level
    audit_logger.setLevel(level if previous_level == logging.NOTSET else min(previous_level, level))
    root.addHandler(handler)
    return handler


def shutdown_file_logging(handler: logging.Handler) -> None:
    """Detach and close a handler previously returned by configure_file_logging."""
    root = logging.getLogger()
    root.removeHandler(handler)
    audit_logger = logging.getLogger("ppt_agent")
    previous = getattr(handler, "_previous_audit_logger_level", None)
    if previous is not None:
        audit_logger.setLevel(previous)
    for name, disabled in getattr(handler, "_previous_disabled_loggers", {}).items():
        logging.getLogger(name).disabled = disabled
    handler.flush()
    handler.close()
