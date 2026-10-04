import asyncio
import json
from pathlib import Path
from uuid import uuid4

from ppt_agent.runtime.application import ApplicationRuntime, ApplicationRuntimeConfig
from ppt_agent.storage.capabilities import FilesystemKind


def test_runtime_writes_startup_and_shutdown_records_without_changing_readiness() -> None:
    workspace = Path(".cache/test-application-logging") / str(uuid4())

    async def execute(_run_id: str) -> None:
        return None

    async def scenario() -> None:
        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(workspace=workspace, minimum_free_bytes=0),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        assert runtime.ready is True
        assert runtime._file_log_handler is not None
        await runtime.stop()
        assert runtime.ready is False

    asyncio.run(scenario())
    records = [
        json.loads(line)
        for line in (workspace / "logs" / "ppt-agent.jsonl").read_text().splitlines()
    ]
    assert [record["message"] for record in records] == [
        "runtime startup",
        "runtime shutdown",
    ]
