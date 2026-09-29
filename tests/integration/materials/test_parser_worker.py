import time
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from ppt_agent.api.security import InputLimits
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.worker import ParserWorker, _configure_child_environment


def slow_child(connection, payload, temp_dir):
    time.sleep(5)


def crash_child(connection, payload, temp_dir):
    raise RuntimeError("child crash details must not leak")


def invalid_child(connection, payload, temp_dir):
    connection.send_bytes(b"not-json")


def request(path: Path, timeout: float = 2.0) -> ParseRequest:
    limits = replace(InputLimits(), parser_timeout_seconds=timeout)
    return ParseRequest(
        material_id="material-worker", source_artifact_id="artifact-worker",
        source_path=str(path), media_type="text/markdown", limits=asdict(limits),
    )


def test_worker_parses_in_spawned_process_and_cleans_temp_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.md"
    source.write_text("# Worker", encoding="utf-8")
    worker = ParserWorker(workspace)

    parsed = worker.parse(request(source))

    assert parsed.text_blocks[0].text == "Worker"
    assert list((workspace / ".parser-tmp").iterdir()) == []


def test_child_environment_is_confined_to_attempt_temp_directory(tmp_path: Path) -> None:
    original_cwd = Path.cwd()
    original_tempdir = tempfile.tempdir
    original_environment = {name: os.environ.get(name) for name in ("TMP", "TEMP", "TMPDIR")}
    try:
        _configure_child_environment(str(tmp_path))
        assert Path.cwd() == tmp_path
        assert tempfile.gettempdir() == str(tmp_path)
        assert all(os.environ[name] == str(tmp_path) for name in original_environment)
    finally:
        os.chdir(original_cwd)
        tempfile.tempdir = original_tempdir
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_worker_terminates_timeout_and_maps_child_crash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.md"
    source.write_text("text", encoding="utf-8")
    with pytest.raises(ParserFailure) as caught:
        ParserWorker(workspace, process_target=slow_child).parse(request(source, timeout=0.1))
    assert caught.value.code is MaterialErrorCode.PARSER_TIMEOUT

    with pytest.raises(ParserFailure) as caught:
        ParserWorker(workspace, process_target=crash_child).parse(request(source))
    assert caught.value.code is MaterialErrorCode.PARSER_CRASHED
    assert "child crash details" not in caught.value.safe_detail


def test_worker_rejects_invalid_result_schema(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.md"
    source.write_text("text", encoding="utf-8")
    with pytest.raises(ParserFailure) as caught:
        ParserWorker(workspace, process_target=invalid_child).parse(request(source))
    assert caught.value.code is MaterialErrorCode.PARSER_OUTPUT_INVALID


def test_worker_rejects_oversized_result_envelope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.md"
    source.write_text("# envelope", encoding="utf-8")
    with pytest.raises(ParserFailure) as caught:
        ParserWorker(workspace, max_result_bytes=16).parse(request(source))
    assert caught.value.code is MaterialErrorCode.PARSER_OUTPUT_INVALID


def test_worker_rejects_source_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes Workspace"):
        ParserWorker(workspace).parse(request(outside))


def test_worker_cancel_terminates_child_and_joins_parse_thread(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.md"
    source.write_text("text", encoding="utf-8")
    worker = ParserWorker(workspace, process_target=slow_child)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(worker.parse, request(source, timeout=5))
        deadline = time.monotonic() + 2
        while not worker.active_material_ids and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.cancel("material-worker") is True
        with pytest.raises(ParserFailure):
            result.result(timeout=2)
    assert worker.active_material_ids == ()
