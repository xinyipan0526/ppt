import asyncio
from threading import Event

import pytest

from ppt_agent.runtime.export_supervisor import ExportSupervisor


class FakeExports:
    def __init__(self, statuses: dict[str, str], *, block: Event | None = None) -> None:
        self.statuses = statuses
        self.block = block
        self.calls: list[str] = []

    def recoverable_ids(self) -> tuple[str, ...]:
        return tuple(key for key, value in self.statuses.items() if value in {"queued", "running"})

    def status(self, export_id: str):
        return type(
            "Snapshot",
            (),
            {
                "status": self.statuses[export_id],
                "attempt_count": self.attempt_counts[export_id],
            },
        )()

    def export(self, export_id: str, **_kwargs) -> str:
        self.calls.append(export_id)
        self.attempt_counts[export_id] += 1
        self.statuses[export_id] = "running"
        if self.block is not None:
            self.block.wait(timeout=5)
        self.statuses[export_id] = "available"
        return "artifact"

    def fail(
        self,
        export_id: str,
        *,
        code: str,
        expected_attempt_count: int | None = None,
    ) -> None:
        if expected_attempt_count is not None and (
            self.statuses[export_id] != "running"
            or self.attempt_counts[export_id] != expected_attempt_count
        ):
            return
        self.statuses[export_id] = "failed"

    @property
    def attempt_counts(self) -> dict[str, int]:
        if not hasattr(self, "_attempt_counts"):
            self._attempt_counts = {key: 0 for key in self.statuses}
        return self._attempt_counts


def test_supervisor_deduplicates_and_recovers_only_recoverable_states() -> None:
    async def scenario() -> None:
        exports = FakeExports({
            "queued": "queued", "running": "running", "available": "available",
            "failed": "failed", "cancelled": "cancelled",
        })
        supervisor = ExportSupervisor(exports)
        assert supervisor.schedule("queued") is True
        assert supervisor.schedule("queued") is False
        await supervisor.shutdown(timeout=1)
        assert exports.calls == ["queued"]

        recoverable = FakeExports({
            "queued-recovery": "queued", "running-recovery": "running",
            "available": "available", "failed": "failed", "cancelled": "cancelled",
        })
        recovered = ExportSupervisor(recoverable)
        scheduled = recovered.recover()
        await recovered.shutdown(timeout=1)
        assert scheduled == ("queued-recovery", "running-recovery")
        assert recoverable.calls == ["queued-recovery", "running-recovery"]

    asyncio.run(scenario())


def test_supervisor_shutdown_is_bounded_and_leaves_work_recoverable() -> None:
    async def scenario() -> None:
        block = Event()
        exports = FakeExports({"e": "queued"}, block=block)
        supervisor = ExportSupervisor(exports)
        assert supervisor.schedule("e") is True
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="worker.*still running"):
            await supervisor.shutdown(timeout=0)
        assert exports.statuses["e"] in {"queued", "running"}
        block.set()
        while supervisor.active_export_ids:
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_supervisor_does_not_report_shutdown_while_worker_thread_is_alive() -> None:
    """Catches disposing runtime resources while ``to_thread`` still runs."""

    async def scenario() -> None:
        started = Event()
        release = Event()
        finished = Event()

        class NonCooperative(FakeExports):
            def export(self, export_id: str, **_kwargs) -> str:
                self.calls.append(export_id)
                started.set()
                try:
                    release.wait(timeout=5)
                    return "artifact"
                finally:
                    finished.set()

        exports = NonCooperative({"e": "running"})
        supervisor = ExportSupervisor(exports)
        supervisor.schedule("e")
        assert await asyncio.to_thread(started.wait, 2)

        with pytest.raises(RuntimeError, match="worker.*still running"):
            await supervisor.shutdown(timeout=0.01)
        assert finished.is_set() is False
        assert exports.statuses["e"] == "running"

        release.set()
        while supervisor.active_export_ids:
            await asyncio.sleep(0)
        assert finished.is_set() is True
        assert await supervisor.shutdown(timeout=1) == ()

    asyncio.run(scenario())


def test_supervisor_persists_and_logs_only_safe_failure_data(caplog) -> None:
    class Broken(FakeExports):
        error: dict | None = None

        def export(self, export_id: str, **_kwargs) -> str:
            self.attempt_counts[export_id] += 1
            self.statuses[export_id] = "running"
            raise RuntimeError("C:/secret/raw payload")

        def fail(
            self,
            export_id: str,
            *,
            code: str,
            expected_attempt_count: int | None = None,
        ) -> None:
            super().fail(
                export_id,
                code=code,
                expected_attempt_count=expected_attempt_count,
            )
            self.error = {"code": code}

    async def scenario() -> None:
        exports = Broken({"e": "queued"})
        supervisor = ExportSupervisor(exports)
        with caplog.at_level("ERROR", logger="ppt_agent.runtime.export_supervisor"):
            supervisor.schedule("e")
            await supervisor.shutdown(timeout=1)
        assert exports.statuses["e"] == "failed"
        assert exports.error == {"code": "export_failed"}
        assert "secret" not in str(exports.error)
        assert "secret" not in caplog.text
        failure = next(
            record for record in caplog.records
            if record.getMessage() == "export executor failed"
        )
        assert failure.export_id == "e"
        assert failure.exception_type == "RuntimeError"
        assert failure.exc_info is None

    asyncio.run(scenario())


def test_supervisor_eventually_schedules_same_id_requeued_during_old_teardown() -> None:
    async def scenario() -> None:
        first_started = Event()
        release_first = Event()

        class RequeuedDuringTeardown(FakeExports):
            def export(self, export_id: str, **_kwargs) -> str:
                self.calls.append(export_id)
                if len(self.calls) == 1:
                    first_started.set()
                    release_first.wait(timeout=5)
                    # The explicit retry has already changed the durable row
                    # back to queued while this old owner is winding down.
                    return "old-attempt"
                self.statuses[export_id] = "available"
                return "new-attempt"

        exports = RequeuedDuringTeardown({"e": "running"})
        supervisor = ExportSupervisor(exports)
        assert supervisor.schedule("e") is True
        assert await asyncio.to_thread(first_started.wait, 2)

        exports.statuses["e"] = "queued"
        assert supervisor.schedule("e") is False
        release_first.set()

        for _ in range(100):
            if exports.statuses["e"] == "available":
                break
            await asyncio.sleep(0.01)
        assert exports.statuses["e"] == "available"
        assert exports.calls == ["e", "e"]
        await supervisor.shutdown(timeout=1)

    asyncio.run(scenario())


def test_late_old_owner_failure_cannot_overwrite_explicit_same_id_requeue() -> None:
    async def scenario() -> None:
        first_started = Event()
        release_first = Event()

        class LateFailure(FakeExports):
            def status(self, export_id: str):
                return type(
                    "Snapshot",
                    (),
                    {
                        "status": self.statuses[export_id],
                        "attempt_count": self.attempt_counts[export_id],
                    },
                )()

            def export(self, export_id: str, **_kwargs) -> str:
                self.calls.append(export_id)
                self.attempt_counts[export_id] += 1
                self.statuses[export_id] = "running"
                if len(self.calls) == 1:
                    first_started.set()
                    release_first.wait(timeout=5)
                    raise RuntimeError("old owner failed late")
                self.statuses[export_id] = "available"
                return "new-attempt"

        exports = LateFailure({"e": "queued"})
        supervisor = ExportSupervisor(exports)
        assert supervisor.schedule("e") is True
        assert await asyncio.to_thread(first_started.wait, 2)

        exports.statuses["e"] = "queued"
        assert supervisor.schedule("e") is False
        release_first.set()

        for _ in range(100):
            if exports.statuses["e"] == "available":
                break
            await asyncio.sleep(0.01)
        assert exports.statuses["e"] == "available"
        assert exports.calls == ["e", "e"]
        assert exports.attempt_counts["e"] == 2
        await supervisor.shutdown(timeout=1)

    asyncio.run(scenario())


def test_reschedule_intent_survives_transient_recoverable_state_read_failure() -> None:
    async def scenario() -> None:
        first_started = Event()
        release_first = Event()

        class FlakyRecovery(FakeExports):
            recovery_reads = 0

            def recoverable_ids(self) -> tuple[str, ...]:
                self.recovery_reads += 1
                if self.recovery_reads == 1:
                    raise RuntimeError("temporary database read failure")
                return super().recoverable_ids()

            def export(self, export_id: str, **_kwargs) -> str:
                self.calls.append(export_id)
                if len(self.calls) == 1:
                    first_started.set()
                    release_first.wait(timeout=5)
                    return "old-attempt"
                self.statuses[export_id] = "available"
                return "new-attempt"

        exports = FlakyRecovery({"e": "running"})
        supervisor = ExportSupervisor(exports)
        assert supervisor.schedule("e") is True
        assert await asyncio.to_thread(first_started.wait, 2)
        exports.statuses["e"] = "queued"
        assert supervisor.schedule("e") is False
        release_first.set()

        for _ in range(200):
            if exports.statuses["e"] == "available":
                break
            await asyncio.sleep(0.01)
        assert exports.statuses["e"] == "available"
        assert exports.calls == ["e", "e"]
        assert exports.recovery_reads >= 2
        await supervisor.shutdown(timeout=1)

    asyncio.run(scenario())


def test_shutdown_cancels_deferred_reschedule_state_check() -> None:
    async def scenario() -> None:
        first_started = Event()
        release_first = Event()

        class BrokenRecovery(FakeExports):
            recovery_reads = 0

            def recoverable_ids(self) -> tuple[str, ...]:
                self.recovery_reads += 1
                raise RuntimeError("database unavailable")

            def export(self, export_id: str, **_kwargs) -> str:
                self.calls.append(export_id)
                first_started.set()
                release_first.wait(timeout=5)
                return "old-attempt"

        exports = BrokenRecovery({"e": "running"})
        supervisor = ExportSupervisor(exports)
        supervisor.schedule("e")
        assert await asyncio.to_thread(first_started.wait, 2)
        exports.statuses["e"] = "queued"
        assert supervisor.schedule("e") is False
        release_first.set()
        for _ in range(100):
            if not supervisor.active_export_ids and exports.recovery_reads:
                break
            await asyncio.sleep(0.01)
        assert exports.recovery_reads == 1

        assert await supervisor.shutdown(timeout=1) == ()
        await asyncio.sleep(0.15)
        assert exports.recovery_reads == 1
        assert exports.calls == ["e"]

    asyncio.run(scenario())
