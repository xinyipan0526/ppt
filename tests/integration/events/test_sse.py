import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from ppt_agent.api.app import create_app
from ppt_agent.events.sse import EventStreamService
from ppt_agent.events.store import EventDurability, WorkflowEventStore
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.models import Base


def create_services() -> tuple[WorkflowRepository, WorkflowEventStore, object]:
    directory = Path(".cache/test-sse")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    return WorkflowRepository(sessions), WorkflowEventStore(sessions), engine


def test_stream_replays_after_last_event_id_with_sse_metadata() -> None:
    async def scenario() -> tuple[str, str]:
        repository, store, engine = create_services()
        try:
            run_id = create_run(repository)
            store.append(
                run_id=run_id,
                event_type="page.progress",
                payload={"percent": 50},
                durability=EventDurability.EPHEMERAL,
            )
            store.append(
                run_id=run_id,
                event_type="page.progress",
                payload={"percent": 75},
                durability=EventDurability.EPHEMERAL,
            )
            service = EventStreamService(store, poll_interval=0.001, batch_size=1)
            stream = service.iter_sse(run_id, after_seq=1)
            first_frame = await anext(stream)
            second_frame = await anext(stream)
            await stream.aclose()
            return first_frame, second_frame
        finally:
            engine.dispose()

    frame, second_frame = asyncio.run(scenario())

    assert "id: 2\n" in frame
    assert "event: page.progress\n" in frame
    assert "retry: 3000\n" in frame
    assert '"schema_version":1' in frame
    assert '"percent":50' in frame
    assert "id: 3\n" in second_frame


def test_expired_cursor_emits_terminal_refresh_instruction() -> None:
    async def scenario() -> tuple[str, bool]:
        repository, store, engine = create_services()
        try:
            run_id = create_run(repository)
            store.append(
                run_id=run_id,
                event_type="page.progress",
                payload={"percent": 50},
                durability=EventDurability.EPHEMERAL,
                occurred_at=datetime.now(UTC) - timedelta(days=30),
            )
            store.prune_ephemeral(before=datetime.now(UTC) + timedelta(days=1))
            service = EventStreamService(store, poll_interval=0.001, batch_size=8)
            stream = service.iter_sse(run_id, after_seq=1)
            frame = await anext(stream)
            try:
                await anext(stream)
            except StopAsyncIteration:
                closed = True
            else:
                closed = False
            return frame, closed
        finally:
            engine.dispose()

    frame, closed = asyncio.run(scenario())

    assert "event: cursor_expired\n" in frame
    assert '"refresh_required":true' in frame
    assert closed is True


def test_snapshot_and_sse_routes_authorize_run_and_honor_last_event_id() -> None:
    async def scenario() -> tuple[dict[str, object], str, list[str]]:
        repository, store, engine = create_services()
        authorized: list[str] = []
        try:
            run_id = create_run(repository)
            store.append(
                run_id=run_id,
                event_type="page.progress",
                payload={"percent": 75},
                durability=EventDurability.EPHEMERAL,
            )

            async def authorize(candidate_run_id: str) -> None:
                authorized.append(candidate_run_id)

            app = create_app(
                event_stream=EventStreamService(
                    store, poll_interval=0.001, batch_size=8
                ),
                authorize_run=authorize,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                snapshot_response = await client.get(f"/api/v1/runs/{run_id}/snapshot")
                async with client.stream(
                    "GET",
                    f"/api/v1/runs/{run_id}/events",
                    headers={"Last-Event-ID": "99"},
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk
                        if "cursor_expired" in body:
                            break
            return snapshot_response.json(), body, authorized
        finally:
            engine.dispose()

    snapshot, body, authorized = asyncio.run(scenario())

    assert snapshot["snapshot_seq"] == 2
    assert snapshot["run"]["status"] == "queued"
    assert "event: cursor_expired" in body
    assert authorized == [snapshot["run"]["id"], snapshot["run"]["id"]]


def test_unknown_run_event_route_returns_404_before_stream_starts() -> None:
    async def scenario() -> tuple[int, dict[str, object]]:
        _repository, store, engine = create_services()
        try:
            async def authorize(_run_id: str) -> None:
                return None

            app = create_app(
                event_stream=EventStreamService(store, poll_interval=0.001),
                authorize_run=authorize,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(f"/api/v1/runs/{uuid4()}/events")
            return response.status_code, response.json()
        finally:
            engine.dispose()

    status_code, body = asyncio.run(scenario())

    assert status_code == 404
    assert "workflow run not found" in str(body["detail"])


def create_run(repository: WorkflowRepository) -> str:
    project_id = repository.create_project("SSE protocol")
    return repository.create_run(project_id, "deck_generation", 1).id
