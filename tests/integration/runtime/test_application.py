import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.domain.state_machine import RunEvent
from ppt_agent.runtime.application import ApplicationRuntime, ApplicationRuntimeConfig
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.capabilities import FilesystemKind
from ppt_agent.storage.database import create_session_factory, create_sqlite_engine, DatabaseConfig
from ppt_agent.storage.locking import WorkspaceLockUnavailable
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
