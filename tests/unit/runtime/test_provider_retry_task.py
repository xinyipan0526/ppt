import asyncio
import logging
import threading
from datetime import datetime

from ppt_agent.runtime.maintenance import ProviderRetryTask


def test_provider_retry_logs_first_failure_and_throttled_summaries(caplog) -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = 0
            self.failed_four_times = threading.Event()

        def release_due_provider_retries(self, _now: object) -> tuple[str, ...]:
            self.calls += 1
            if self.calls >= 4:
                self.failed_four_times.set()
            raise RuntimeError("raw provider payload ark-secret")

    class Supervisor:
        def schedule(self, _run_id: str) -> None:
            raise AssertionError("failed repository must not schedule a retry")

    async def scenario() -> None:
        repository = Repository()
        maintenance = ProviderRetryTask(
            repository,  # type: ignore[arg-type]
            Supervisor(),  # type: ignore[arg-type]
            interval=0.001,
        )
        maintenance.start()
        await asyncio.to_thread(repository.failed_four_times.wait, 1)
        assert await maintenance.stop(timeout=1) is True

    with caplog.at_level(logging.WARNING, logger="ppt_agent.runtime.maintenance"):
        asyncio.run(scenario())

    records = [
        record for record in caplog.records
        if record.name == "ppt_agent.runtime.maintenance"
    ]
    assert any(record.getMessage() == "maintenance task failed" for record in records)
    assert any(
        record.getMessage() == "maintenance task failure summary" for record in records
    )
    assert all("ark-secret" not in record.getMessage() for record in records)
    assert all(getattr(record, "maintenance_task", None) == "provider_retry" for record in records)
    assert all(
        datetime.fromisoformat(record.next_run_at).tzinfo is not None
        for record in records
    )
