import json
import logging
from pathlib import Path

from ppt_agent.observability.logging import configure_file_logging, shutdown_file_logging


def test_file_logger_writes_bounded_json_and_redacts_nested_context(tmp_path: Path) -> None:
    handler = configure_file_logging(tmp_path, max_bytes=10_000, backup_count=2)
    try:
        logger = logging.getLogger("test.audit")
        logger.setLevel(logging.INFO)
        logger.info(
            "provider request completed",
            extra={
                "project_id": "project-1",
                "run_id": "run-1",
                "nested": {
                    "api_key": "top-secret-key",
                    "safe": "ok",
                    "source_content": "private source text",
                },
                "prompt": "private prompt",
                "secret_path": str(tmp_path / ".secrets" / "ark"),
                "unbounded": "x" * 10_000,
            },
        )
        handler.flush()
        record = json.loads((tmp_path / "logs" / "ppt-agent.jsonl").read_text())
    finally:
        logger.setLevel(logging.NOTSET)
        shutdown_file_logging(handler)

    assert record["logger"] == "test.audit"
    assert record["message"] == "provider request completed"
    assert record["project_id"] == "project-1"
    assert record["nested"]["api_key"] == "[REDACTED]"
    assert record["nested"]["source_content"] == "[REDACTED]"
    assert record["prompt"] == "[REDACTED]"
    assert record["secret_path"] == "[REDACTED]"
    assert len(record["unbounded"]) <= 512
    assert "top-secret-key" not in (tmp_path / "logs" / "ppt-agent.jsonl").read_text()


def test_file_logger_sanitizes_exception_text_and_configures_rotation(tmp_path: Path) -> None:
    handler = configure_file_logging(tmp_path, level=logging.WARNING, max_bytes=1234, backup_count=4)
    try:
        assert handler.level == logging.WARNING
        assert handler.maxBytes == 1234
        assert handler.backupCount == 4
        logger = logging.getLogger("test.exception")
        try:
            raise ValueError("authorization: Bearer abc123 api_key=secret-value")
        except ValueError:
            logger.exception("provider failed")
        handler.flush()
        line = (tmp_path / "logs" / "ppt-agent.jsonl").read_text().strip()
        record = json.loads(line)
    finally:
        shutdown_file_logging(handler)

    assert record["level"] == "ERROR"
    assert "secret-value" not in record["exception"]
    assert "abc123" not in record["exception"]
    assert "[REDACTED]" in record["exception"]
