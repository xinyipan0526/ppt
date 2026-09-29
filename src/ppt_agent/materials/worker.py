"""Spawn-isolated parser execution with bounded IPC and cleanup."""

from __future__ import annotations

import json
import math
import multiprocessing
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ppt_agent.domain.materials import MaterialErrorCode, ParsedMaterialV1
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.parsers.image import parse_image
from ppt_agent.materials.parsers.markdown import parse_markdown
from ppt_agent.materials.parsers.pdf import parse_pdf
from ppt_agent.storage.workspace import WorkspacePaths


ProcessTarget = Callable[[Any, dict[str, Any], str], None]


def _apply_posix_limits(payload: dict[str, Any]) -> None:
    try:
        import resource
    except ImportError:  # Windows: process + timeout are the guaranteed boundary.
        return
    limits = dict(payload["limits"])
    timeout = max(1, math.ceil(float(limits["parser_timeout_seconds"])))
    memory = max(128 * 1024 * 1024, int(limits["max_image_pixels"]) * 8)
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def _parse(request: ParseRequest) -> ParsedMaterialV1:
    if request.media_type == "text/markdown":
        return parse_markdown(request)
    if request.media_type == "application/pdf":
        return parse_pdf(request)
    if request.media_type.startswith("image/"):
        return parse_image(request)
    raise ParserFailure(
        MaterialErrorCode.UNSUPPORTED_MEDIA_TYPE,
        "no local parser is registered for this media type",
    )


def _child_entry(connection: Any, payload: dict[str, Any], temp_dir: str) -> None:
    del temp_dir
    try:
        _apply_posix_limits(payload)
        parsed = _parse(ParseRequest.from_payload(payload))
        envelope = {"ok": True, "result": parsed.model_dump(mode="json")}
    except ParserFailure as exc:
        envelope = {"ok": False, "code": exc.code.value, "detail": exc.safe_detail}
    except BaseException:
        envelope = {
            "ok": False,
            "code": MaterialErrorCode.PARSER_CRASHED.value,
            "detail": "parser child failed unexpectedly",
        }
    try:
        connection.send_bytes(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    finally:
        connection.close()


class ParserWorker:
    def __init__(
        self,
        workspace: Path,
        *,
        process_target: ProcessTarget = _child_entry,
        max_result_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        self._paths = WorkspacePaths(workspace)
        self._target = process_target
        self._max_result_bytes = max_result_bytes
        self._temp_root = self._paths.resolve(".parser-tmp")
        self._temp_root.mkdir(exist_ok=True)
        self._paths.verify(self._temp_root)

    def parse(self, request: ParseRequest) -> ParsedMaterialV1:
        source = self._paths.verify(Path(request.source_path), must_exist=True)
        request = ParseRequest(
            material_id=request.material_id,
            source_artifact_id=request.source_artifact_id,
            source_path=str(source),
            media_type=request.media_type,
            limits=dict(request.limits),
        )
        attempt_temp = Path(tempfile.mkdtemp(prefix="parse-", dir=self._temp_root))
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=self._target,
            args=(child, request.to_payload(), str(attempt_temp)),
            daemon=True,
        )
        timeout = float(request.limits["parser_timeout_seconds"])
        try:
            process.start()
            child.close()
            if not parent.poll(timeout):
                if process.is_alive():
                    self._stop(process)
                    raise ParserFailure(
                        MaterialErrorCode.PARSER_TIMEOUT, "parser exceeded its time limit"
                    )
                process.join(timeout=1)
                raise ParserFailure(
                    MaterialErrorCode.PARSER_CRASHED, "parser child exited without a result"
                )
            try:
                raw = parent.recv_bytes(self._max_result_bytes)
            except EOFError as exc:
                process.join(timeout=1)
                code = (
                    MaterialErrorCode.PARSER_CRASHED
                    if process.exitcode not in (None, 0)
                    else MaterialErrorCode.PARSER_OUTPUT_INVALID
                )
                raise ParserFailure(code, "parser child exited without a valid result") from exc
            except OSError as exc:
                self._stop(process)
                raise ParserFailure(
                    MaterialErrorCode.PARSER_OUTPUT_INVALID,
                    "parser result envelope is missing or too large",
                ) from exc
            process.join(timeout=1)
            if process.is_alive():
                self._stop(process)
            return self._decode(raw)
        finally:
            parent.close()
            if process.is_alive():
                self._stop(process)
            shutil.rmtree(attempt_temp, ignore_errors=True)

    @staticmethod
    def _stop(process: multiprocessing.Process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=1)

    @staticmethod
    def _decode(raw: bytes) -> ParsedMaterialV1:
        try:
            envelope = json.loads(raw.decode("utf-8"))
            if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
                raise ValueError("invalid envelope")
            if not envelope["ok"]:
                code = MaterialErrorCode(str(envelope["code"]))
                detail = str(envelope.get("detail", "parser failed"))[:500]
                raise ParserFailure(code, detail)
            return ParsedMaterialV1.model_validate(envelope["result"])
        except ParserFailure:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise ParserFailure(
                MaterialErrorCode.PARSER_OUTPUT_INVALID,
                "parser returned an invalid result envelope",
            ) from exc
