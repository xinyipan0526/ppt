import asyncio
from pathlib import Path
from uuid import uuid4

from ppt_agent.domain.state_machine import RunEvent, RunStatus
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.runtime.supervisor import SupervisorClosed, TaskSupervisor
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine
from ppt_agent.storage.models import Base


def make_repository() -> tuple[WorkflowRepository, object]:
    directory = Path(".cache/test-supervisor")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    return WorkflowRepository(create_session_factory(engine)), engine


def test_supervisor_deduplicates_and_limits_graceful_shutdown() -> None:
    async def scenario() -> tuple[bool, tuple[str, ...], RunStatus, bool]:
        repository, engine = make_repository()
        entered = asyncio.Event()

        async def executor(_run_id: str) -> None:
            entered.set()
            await asyncio.Event().wait()

        project = repository.create_project("Supervisor")
        run = repository.create_run(project, "deck", 1)
        supervisor = TaskSupervisor(repository, executor, global_limit=1)
        try:
            first = supervisor.schedule(run.id)
            duplicate = supervisor.schedule(run.id)
            await entered.wait()
            interrupted = await supervisor.shutdown(timeout=0)
            restored = repository.get_run(run.id)
            try:
                supervisor.schedule(run.id)
            except SupervisorClosed:
                closed = True
            else:
                closed = False
            return first and not duplicate, interrupted, restored.status, closed
        finally:
            engine.dispose()

    deduplicated, interrupted, status, closed = asyncio.run(scenario())

    assert deduplicated is True
    assert len(interrupted) == 1
    assert status is RunStatus.RECOVERY_REQUIRED
    assert closed is True


def test_supervisor_isolates_executor_failure_as_recovery_required() -> None:
    async def scenario() -> RunStatus:
        repository, engine = make_repository()

        async def executor(_run_id: str) -> None:
            raise RuntimeError("boom")

        project = repository.create_project("Supervisor")
        run = repository.create_run(project, "deck", 1)
        supervisor = TaskSupervisor(repository, executor)
        try:
            supervisor.schedule(run.id)
            while supervisor.active_run_ids:
                await asyncio.sleep(0)
            return repository.get_run(run.id).status
        finally:
            engine.dispose()

    assert asyncio.run(scenario()) is RunStatus.RECOVERY_REQUIRED


def test_cancel_requested_run_never_reenters_normal_executor() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        repository, engine = make_repository()
        normal: list[str] = []
        cancelling: list[str] = []

        async def executor(run_id: str) -> None:
            normal.append(run_id)

        async def cancellation_executor(run_id: str) -> None:
            cancelling.append(run_id)

        project = repository.create_project("Supervisor")
        run = repository.create_run(project, "deck", 1)
        repository.transition_run(run.id, RunEvent.CANCEL_REQUESTED)
        supervisor = TaskSupervisor(
            repository,
            executor,
            cancellation_executor=cancellation_executor,
        )
        try:
            supervisor.schedule(run.id)
            while supervisor.active_run_ids:
                await asyncio.sleep(0)
            return normal, cancelling
        finally:
            engine.dispose()

    normal, cancelling = asyncio.run(scenario())
    assert normal == []
    assert len(cancelling) == 1


def test_active_run_cancel_executes_durable_cancellation_completion() -> None:
    async def scenario() -> RunStatus:
        repository, engine = make_repository()
        entered = asyncio.Event()

        async def executor(_run_id: str) -> None:
            entered.set()
            await asyncio.Event().wait()

        async def cancellation_executor(run_id: str) -> None:
            repository.complete_cancellation(run_id)

        project = repository.create_project("Supervisor")
        run = repository.create_run(project, "deck", 1)
        supervisor = TaskSupervisor(
            repository, executor, cancellation_executor=cancellation_executor
        )
        try:
            supervisor.schedule(run.id)
            await entered.wait()
            assert await supervisor.cancel(run.id)
            return repository.get_run(run.id).status
        finally:
            engine.dispose()

    assert asyncio.run(scenario()) is RunStatus.CANCELLED
