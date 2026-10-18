import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta

from ppt_agent.runtime.maintenance import EventRetentionTask, _MaintenanceFailureReporter


def test_event_retention_runs_immediately_and_stops() -> None:
    class Store:
        def __init__(self) -> None:
            self.called = threading.Event()

        def prune_ephemeral(self, *, before: object) -> int:
            assert before is not None
            self.called.set()
            return 0

    async def scenario() -> None:
        store = Store()
        maintenance = EventRetentionTask(
            store,  # type: ignore[arg-type]
            retention=timedelta(days=1),
            interval=60,
        )
        maintenance.start()
        await asyncio.to_thread(store.called.wait, 2)
        assert store.called.is_set()
        assert await maintenance.stop(timeout=1) is True

    asyncio.run(scenario())


def test_event_retention_shutdown_is_bounded() -> None:
    class Store:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def prune_ephemeral(self, *, before: object) -> int:
            self.entered.set()
            self.release.wait(2)
            return 0

    async def scenario() -> bool:
        store = Store()
        maintenance = EventRetentionTask(
            store,  # type: ignore[arg-type]
            retention=timedelta(days=1),
            interval=60,
        )
        maintenance.start()
        await asyncio.to_thread(store.entered.wait, 1)
        stopped = await maintenance.stop(timeout=0.01)
        store.release.set()
        return stopped

    assert asyncio.run(scenario()) is False


def test_event_retention_logs_first_failure_and_throttled_summaries(caplog) -> None:
    class Store:
        def __init__(self) -> None:
            self.calls = 0
            self.failed_four_times = threading.Event()

        def prune_ephemeral(self, *, before: object) -> int:
            self.calls += 1
            if self.calls >= 4:
                self.failed_four_times.set()
            raise RuntimeError("provider payload ark-secret")

    async def scenario() -> None:
        store = Store()
        maintenance = EventRetentionTask(
            store,  # type: ignore[arg-type]
            retention=timedelta(days=1),
            interval=0.001,
        )
        maintenance.start()
        await asyncio.to_thread(store.failed_four_times.wait, 1)
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
    assert all(getattr(record, "maintenance_task", None) == "event_retention" for record in records)
    assert all(
        datetime.fromisoformat(record.next_run_at).tzinfo is not None
        for record in records
    )


def test_maintenance_failure_summary_stops_after_the_bounded_count(caplog) -> None:
    reporter = _MaintenanceFailureReporter("event_retention")

    with caplog.at_level(logging.WARNING, logger="ppt_agent.runtime.maintenance"):
        for _ in range(1_026):
            reporter.record_failure(RuntimeError("provider payload ark-secret"))

    records = [
        record for record in caplog.records
        if record.name == "ppt_agent.runtime.maintenance"
    ]
    summaries = [
        record for record in records
        if record.getMessage() == "maintenance task failure summary"
    ]
    assert len(summaries) == 10
    assert summaries[-1].failure_count == 1_024
