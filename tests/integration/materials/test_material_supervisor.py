import asyncio
from types import SimpleNamespace

from ppt_agent.materials.supervisor import MaterialSupervisor, MaterialSupervisorClosed


class SnapshotStore:
    def __init__(self, projects: dict[str, str]) -> None:
        self.projects = projects

    def get(self, material_id: str):
        return SimpleNamespace(project_id=self.projects[material_id])


def test_supervisor_deduplicates_and_enforces_global_and_project_limits() -> None:
    async def scenario() -> None:
        store = SnapshotStore({"a": "p1", "b": "p1", "c": "p2"})
        release = asyncio.Event()
        active = 0
        maximum = 0
        project_active: dict[str, int] = {}
        project_max: dict[str, int] = {}
        two_active = asyncio.Event()

        async def execute(material_id: str) -> None:
            nonlocal active, maximum
            project = store.projects[material_id]
            active += 1
            project_active[project] = project_active.get(project, 0) + 1
            maximum = max(maximum, active)
            if maximum == 2:
                two_active.set()
            project_max[project] = max(project_max.get(project, 0), project_active[project])
            try:
                await release.wait()
            finally:
                active -= 1
                project_active[project] -= 1

        supervisor = MaterialSupervisor(store, execute, global_limit=2, project_limit=1)
        assert supervisor.schedule("a") is True
        assert supervisor.schedule("a") is False
        assert supervisor.schedule("b") is True
        assert supervisor.schedule("c") is True
        await asyncio.wait_for(two_active.wait(), timeout=1)
        assert maximum == 2
        assert project_max["p1"] == 1
        release.set()
        assert await supervisor.shutdown(timeout=1) == ()
        assert supervisor.active_material_ids == ()
        try:
            supervisor.schedule("a")
        except MaterialSupervisorClosed:
            pass
        else:
            raise AssertionError("closed supervisor accepted work")

    asyncio.run(scenario())


def test_supervisor_bounded_shutdown_reports_cancelled_materials() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def execute(_material_id: str) -> None:
            started.set()
            await asyncio.Event().wait()

        supervisor = MaterialSupervisor(
            SnapshotStore({"a": "p1"}), execute, global_limit=1, project_limit=1
        )
        supervisor.schedule("a")
        await started.wait()
        assert await supervisor.shutdown(timeout=0) == ("a",)

    asyncio.run(scenario())
