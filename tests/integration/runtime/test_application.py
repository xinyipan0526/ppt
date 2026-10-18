import asyncio
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ppt_agent.domain.state_machine import RunEvent
from ppt_agent.runtime.application import ApplicationRuntime, ApplicationRuntimeConfig
from ppt_agent.runtime.backup_task import BackupShutdownTimeout
from ppt_agent.runtime.export_supervisor import ExportShutdownTimeout
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.capabilities import FilesystemKind
from ppt_agent.storage.database import create_session_factory, create_sqlite_engine, DatabaseConfig
from ppt_agent.storage.locking import WorkspaceInstanceLock, WorkspaceLockUnavailable
from ppt_agent.storage.workspace import WorkspacePaths
from ppt_agent.storage.maintenance import run_migrations


def workspace_path() -> Path:
    return Path(".cache/test-application") / str(uuid4())


def test_runtime_starts_in_order_holds_lock_and_releases_on_shutdown() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def execute(_run_id: str) -> None:
            calls.append("execute")

        config = ApplicationRuntimeConfig(
            workspace=workspace_path(),
            production=True,
            minimum_free_bytes=0,
            maintenance_interval=60,
        )
        runtime = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        assert runtime.ready is True
        assert runtime.reconciliation_report is not None
        assert runtime.recovery_plan is not None

        competing = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        with pytest.raises(WorkspaceLockUnavailable):
            await competing.start()
        assert competing.ready is False

        assert await runtime.stop() == ()
        assert runtime.ready is False
        await competing.start()
        await competing.stop()
        assert calls == []

    asyncio.run(scenario())


def test_runtime_starts_backup_only_after_readiness_and_stops_it_before_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(),
                minimum_free_bytes=0,
                backup_interval=60,
            ),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )

        original_start = runtime._start_backup_task

        def observed_start(*args, **kwargs):
            assert runtime.ready is True
            calls.append("backup_start")
            return original_start(*args, **kwargs)

        monkeypatch.setattr(runtime, "_start_backup_task", observed_start)
        await runtime.start()
        assert runtime._backup is not None

        async def backup_stop(*, timeout: float):
            calls.append("backup_stop")
            return True

        runtime._backup.stop = backup_stop
        await runtime.stop()
        assert calls == ["backup_start", "backup_stop"]

    asyncio.run(scenario())


def test_backup_shutdown_timeout_retains_runtime_owned_resources() -> None:
    async def scenario() -> None:
        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(),
                minimum_free_bytes=0,
                backup_interval=60,
            ),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        backup = runtime._backup
        assert backup is not None
        original_stop = backup.stop

        async def blocked_stop(*, timeout: float):
            raise BackupShutdownTimeout

        backup.stop = blocked_stop
        with pytest.raises(BackupShutdownTimeout):
            await runtime.stop()
        assert runtime._backup is backup
        assert runtime.engine is not None
        assert runtime.artifacts is not None
        assert runtime._lock is not None

        backup.stop = original_stop
        await runtime.stop()

    asyncio.run(scenario())


def test_backup_thread_timeout_retains_workspace_lock_until_worker_exits() -> None:
    """A live ``to_thread`` backup must keep the runtime's resources owned.

    Replacing ``BackupTask.stop`` with a stub cannot exercise the dangerous
    case: cancelling the awaitable does not stop the thread currently holding
    ArtifactStore's shared maintenance lock.  This uses the actual task and a
    blocking backup creator so an early engine/Workspace-lock release is
    externally observable.
    """

    async def scenario() -> None:
        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(),
                minimum_free_bytes=0,
                backup_interval=0.001,
                backup_shutdown_timeout=0.01,
            ),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        backup = runtime._backup
        assert backup is not None
        entered = Event()
        release = Event()

        def blocked_backup(_database: Path, directory: Path) -> tuple[Path, Path]:
            entered.set()
            assert release.wait(5)
            return directory / "backup.db", directory / "backup.manifest.json"

        backup._create_backup = blocked_backup
        assert await asyncio.to_thread(entered.wait, 2)

        try:
            with pytest.raises(BackupShutdownTimeout):
                await runtime.stop()

            assert runtime.engine is not None
            assert runtime.sessions is not None
            assert runtime.artifacts is not None
            assert runtime._lock is not None

            competing_lock = WorkspaceInstanceLock(
                WorkspacePaths(runtime.config.workspace)
            )
            with pytest.raises(WorkspaceLockUnavailable):
                await asyncio.to_thread(competing_lock.acquire)
        finally:
            release.set()
            assert backup._task is not None
            await asyncio.wait_for(asyncio.shield(backup._task), timeout=2)
            await runtime.stop()

    asyncio.run(scenario())


def test_cancelling_stop_does_not_release_resources_owned_by_backup_thread() -> None:
    """Cancellation has the same ownership rule as a bounded timeout."""

    async def scenario() -> None:
        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(),
                minimum_free_bytes=0,
                backup_interval=0.001,
                backup_shutdown_timeout=1,
            ),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        backup = runtime._backup
        assert backup is not None
        entered = Event()
        release = Event()

        def blocked_backup(_database: Path, directory: Path) -> tuple[Path, Path]:
            entered.set()
            assert release.wait(5)
            return directory / "backup.db", directory / "backup.manifest.json"

        backup._create_backup = blocked_backup
        assert await asyncio.to_thread(entered.wait, 2)
        stop_task = asyncio.create_task(runtime.stop())
        while not backup._stop.is_set():
            await asyncio.sleep(0)
        stop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop_task

        try:
            assert runtime.engine is not None
            assert runtime.sessions is not None
            assert runtime.artifacts is not None
            assert runtime._lock is not None
        finally:
            release.set()
            await runtime.stop()

    asyncio.run(scenario())


def test_failed_start_releases_workspace_lock() -> None:
    async def scenario() -> None:
        async def execute(_run_id: str) -> None:
            return None

        config = ApplicationRuntimeConfig(
            workspace=workspace_path(),
            minimum_free_bytes=0,
        )

        def fail_migration(_path: Path) -> None:
            raise RuntimeError("migration failed")

        failed = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
            migration_runner=fail_migration,
        )
        with pytest.raises(RuntimeError, match="migration failed"):
            await failed.start()

        replacement = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await replacement.start()
        await replacement.stop()

    asyncio.run(scenario())


def test_cancelling_recovery_requires_dedicated_executor() -> None:
    async def scenario() -> None:
        workspace = workspace_path()
        workspace.mkdir(parents=True)
        database = workspace / "state.db"
        run_migrations(database)
        engine = create_sqlite_engine(DatabaseConfig(path=database))
        repository = WorkflowRepository(create_session_factory(engine))
        project = repository.create_project("Cancellation")
        run = repository.create_run(project, "deck", 1)
        repository.transition_run(run.id, RunEvent.CANCEL_REQUESTED)
        engine.dispose()

        async def execute(_run_id: str) -> None:
            raise AssertionError("normal executor must not receive cancellation")

        config = ApplicationRuntimeConfig(
            workspace=workspace,
            minimum_free_bytes=0,
        )
        missing = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        with pytest.raises(RuntimeError, match="cancellation executor"):
            await missing.start()

        cancelled: list[str] = []

        async def cancel(run_id: str) -> None:
            cancelled.append(run_id)

        configured = ApplicationRuntime(
            config,
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
            cancellation_executor=cancel,
        )
        await configured.start()
        while configured.supervisor and configured.supervisor.active_run_ids:
            await asyncio.sleep(0)
        await configured.stop()
        assert cancelled == [run.id]

    asyncio.run(scenario())


def test_runtime_can_compose_supervisor_from_planning_workflow_factory() -> None:
    async def scenario() -> None:
        built_with: list[tuple[object, object, object]] = []

        class PlanningWorkflow:
            async def execute(self, _run_id: str) -> None:
                return None

        workflow = PlanningWorkflow()

        def build(sessions, artifacts, repository):
            built_with.append((sessions, artifacts, repository))
            return workflow

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(),
                minimum_free_bytes=0,
                maintenance_interval=60,
            ),
            planning_workflow_factory=build,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        try:
            assert runtime.planning_workflow is workflow
            assert runtime.supervisor is not None
            assert len(built_with) == 1
            assert built_with[0][1] is runtime.artifacts
            assert built_with[0][2] is runtime.repository
        finally:
            await runtime.stop()

        assert runtime.planning_workflow is None

    asyncio.run(scenario())


def test_runtime_starts_material_component_after_artifact_reconciliation() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        class MaterialRuntime:
            async def start(self) -> None:
                assert runtime.reconciliation_report is not None
                assert runtime.recovery_plan is None
                calls.append("material_start")

            async def stop(self, *, timeout: float) -> tuple[str, ...]:
                calls.append(f"material_stop:{timeout}")
                return ()

        component = MaterialRuntime()

        def build_materials(_sessions, _artifacts):
            calls.append("material_build")
            return component

        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(), minimum_free_bytes=0,
                shutdown_timeout=2, maintenance_interval=60,
            ),
            execute,
            material_runtime_factory=build_materials,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        assert runtime.material_runtime is component
        assert runtime.recovery_plan is not None
        await runtime.stop()
        assert calls == ["material_build", "material_start", "material_stop:2"]
        assert runtime.material_runtime is None

    asyncio.run(scenario())


def test_runtime_recovers_exports_before_readiness_and_stops_them_first() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        class Exports:
            def recoverable_ids(self) -> tuple[str, ...]:
                assert runtime.reconciliation_report is not None
                assert runtime.ready is False
                calls.append("export_recover")
                return ("queued-export",)

            def status(self, export_id: str) -> SimpleNamespace:
                assert export_id == "queued-export"
                return SimpleNamespace(status="queued", attempt_count=0)

            def export(self, export_id: str, **_kwargs) -> str:
                calls.append(f"export:{export_id}")
                return "artifact"

            def fail(self, export_id: str, *, code: str, **_kwargs) -> None:
                raise AssertionError((export_id, code))

        exports = Exports()

        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(), minimum_free_bytes=0,
                maintenance_interval=60,
            ),
            execute,
            export_workflow_factory=lambda _sessions, _artifacts: exports,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        assert runtime.ready is True
        assert runtime.export_supervisor is not None
        while runtime.export_supervisor.active_export_ids:
            await asyncio.sleep(0)
        assert calls == ["export_recover", "export:queued-export"]
        original_export_shutdown = runtime.export_supervisor.shutdown
        original_run_shutdown = runtime.supervisor.shutdown

        async def export_shutdown(*, timeout: float):
            calls.append("export_stop")
            return await original_export_shutdown(timeout=timeout)

        async def run_shutdown(*, timeout: float):
            calls.append("run_stop")
            return await original_run_shutdown(timeout=timeout)

        runtime.export_supervisor.shutdown = export_shutdown
        runtime.supervisor.shutdown = run_shutdown
        await runtime.stop()
        assert runtime.export_supervisor is None
        assert calls[-2:] == ["export_stop", "run_stop"]

    asyncio.run(scenario())


def test_export_recovery_failure_cleans_up_and_releases_runtime_lock() -> None:
    async def scenario() -> None:
        workspace = workspace_path()

        class BrokenExports:
            def recoverable_ids(self) -> tuple[str, ...]:
                raise RuntimeError("recovery scan failed")

        async def execute(_run_id: str) -> None:
            return None

        failed = ApplicationRuntime(
            ApplicationRuntimeConfig(workspace=workspace, minimum_free_bytes=0),
            execute,
            export_workflow_factory=lambda _sessions, _artifacts: BrokenExports(),
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        with pytest.raises(RuntimeError, match="recovery scan failed"):
            await failed.start()
        assert failed.export_supervisor is None

        replacement = ApplicationRuntime(
            ApplicationRuntimeConfig(workspace=workspace, minimum_free_bytes=0),
            execute,
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await replacement.start()
        await replacement.stop()

    asyncio.run(scenario())


def test_export_shutdown_timeout_retains_owned_resources_until_retry() -> None:
    """Catches disposing the engine while an export worker may still use it."""

    async def scenario() -> None:
        class Exports:
            def recoverable_ids(self) -> tuple[str, ...]:
                return ()

            def export(self, export_id: str, **_kwargs) -> str:
                raise AssertionError(export_id)

            def fail(self, export_id: str, *, code: str, **_kwargs) -> None:
                raise AssertionError((export_id, code))

        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(), minimum_free_bytes=0,
            ),
            execute,
            export_workflow_factory=lambda _sessions, _artifacts: Exports(),
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        assert runtime.export_supervisor is not None
        original_shutdown = runtime.export_supervisor.shutdown

        async def blocked_shutdown(*, timeout: float):
            raise ExportShutdownTimeout(("still-running",))

        runtime.export_supervisor.shutdown = blocked_shutdown
        with pytest.raises(ExportShutdownTimeout):
            await runtime.stop()
        assert runtime.export_supervisor is not None
        assert runtime.engine is not None
        assert runtime.sessions is not None
        assert runtime.artifacts is not None
        assert runtime._lock is not None

        runtime.export_supervisor.shutdown = original_shutdown
        await runtime.stop()

    asyncio.run(scenario())


def test_cancelled_runtime_stop_retains_export_owned_resources_until_retry() -> None:
    """Catches cancellation disposing resources below a live export worker."""

    async def scenario() -> None:
        class Exports:
            def recoverable_ids(self) -> tuple[str, ...]:
                return ()

            def export(self, export_id: str, **_kwargs) -> str:
                raise AssertionError(export_id)

            def fail(self, export_id: str, *, code: str, **_kwargs) -> None:
                raise AssertionError((export_id, code))

        async def execute(_run_id: str) -> None:
            return None

        runtime = ApplicationRuntime(
            ApplicationRuntimeConfig(
                workspace=workspace_path(), minimum_free_bytes=0,
            ),
            execute,
            export_workflow_factory=lambda _sessions, _artifacts: Exports(),
            filesystem_classifier=lambda _path: FilesystemKind.LOCAL,
        )
        await runtime.start()
        supervisor = runtime.export_supervisor
        assert supervisor is not None
        original_shutdown = supervisor.shutdown
        entered = asyncio.Event()
        release = asyncio.Event()

        async def cancellable_shutdown(*, timeout: float):
            entered.set()
            await release.wait()
            return ()

        supervisor.shutdown = cancellable_shutdown
        stop_task = asyncio.create_task(runtime.stop())
        await entered.wait()
        stop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop_task

        try:
            assert runtime.export_supervisor is supervisor
            assert runtime.engine is not None
            assert runtime.sessions is not None
            assert runtime.artifacts is not None
            assert runtime._lock is not None
        finally:
            if runtime.export_supervisor is supervisor:
                supervisor.shutdown = original_shutdown
                await runtime.stop()

    asyncio.run(scenario())
