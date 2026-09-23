import asyncio
import threading
from datetime import timedelta

from ppt_agent.runtime.maintenance import EventRetentionTask


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
