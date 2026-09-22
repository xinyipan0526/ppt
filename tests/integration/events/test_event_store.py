from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import event

from ppt_agent.domain.state_machine import RunEvent, RunStatus
from ppt_agent.events.store import (
    CursorExpired,
    EventDurability,
    EventPayloadRejected,
    WorkflowEventStore,
)
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.models import Base


def create_stores() -> tuple[WorkflowRepository, WorkflowEventStore, object]:
    directory = Path(".cache/test-events")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(
        DatabaseConfig(path=directory / f"{uuid4()}.db", busy_timeout_ms=10_000)
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    return WorkflowRepository(sessions), WorkflowEventStore(sessions), engine


def create_run(repository: WorkflowRepository) -> str:
    project_id = repository.create_project("Event protocol")
    return repository.create_run(project_id, "deck_generation", 1).id


def test_snapshot_then_replay_does_not_lose_intervening_event() -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        snapshot = store.get_run_snapshot(run_id)

        appended = store.append(
            run_id=run_id,
            event_type="page.progress",
            payload={"page_id": "page-1", "percent": 25},
            page_id="page-1",
            trace_id="trace-1",
            durability=EventDurability.EPHEMERAL,
        )
        replay = store.read_after(run_id, snapshot.snapshot_seq)
    finally:
        engine.dispose()

    assert snapshot.run.id == run_id
    assert snapshot.snapshot_seq == 1
    assert [event.event_id for event in replay.events] == [appended.event_id]
    assert replay.next_cursor == appended.run_seq
    assert replay.cursor_expired is False
    assert appended.schema_version == 1
    assert appended.occurred_at.tzinfo is not None
    assert appended.recorded_at.tzinfo is not None
    assert appended.page_id == "page-1"
    assert appended.trace_id == "trace-1"


def test_concurrent_appends_allocate_unique_contiguous_run_sequences() -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)

        def append(index: int) -> int:
            return store.append(
                run_id=run_id,
                event_type="page.progress",
                payload={"page": index},
                durability=EventDurability.EPHEMERAL,
            ).run_seq

        with ThreadPoolExecutor(max_workers=8) as executor:
            sequences = list(executor.map(append, range(24)))
        persisted = repository.list_events(run_id)
    finally:
        engine.dispose()

    assert sorted(sequences) == list(range(2, 26))
    assert [event.run_seq for event in persisted] == list(range(1, 26))


def test_pruning_ephemeral_events_expires_cursor_but_preserves_core_audit() -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        old = datetime.now(UTC) - timedelta(days=30)
        ephemeral = store.append(
            run_id=run_id,
            event_type="page.progress",
            payload={"percent": 10},
            durability=EventDurability.EPHEMERAL,
            occurred_at=old,
        )
        core = store.append(
            run_id=run_id,
            event_type="budget.reserved",
            payload={"reservation_id": "reservation-1"},
            durability=EventDurability.CORE,
            occurred_at=old,
        )

        deleted = store.prune_ephemeral(before=datetime.now(UTC) + timedelta(days=1))
        replay = store.read_after(run_id, 1)
        current = store.read_after(run_id, ephemeral.run_seq)
    finally:
        engine.dispose()

    assert deleted == 1
    assert replay.cursor_expired is True
    assert replay.events == ()
    assert current.cursor_expired is False
    assert [event.event_id for event in current.events] == [core.event_id]


def test_unknown_future_cursor_is_rejected() -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        with pytest.raises(CursorExpired, match="ahead"):
            store.read_after(run_id, 99)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "secret"},
        {"nested": {"prompt": "full prompt"}},
        {"response": "full provider response"},
    ],
)
def test_event_payload_rejects_sensitive_fields(payload: dict[str, object]) -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        with pytest.raises(EventPayloadRejected):
            store.append(
                run_id=run_id,
                event_type="provider.debug",
                payload=payload,
                durability=EventDurability.EPHEMERAL,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": ({"prompt": "secret"},)},
        {"api key": "secret"},
        {"file content": "secret"},
    ],
)
def test_event_payload_rejects_sensitive_fields_in_all_json_containers(
    payload: dict[str, object],
) -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        with pytest.raises(EventPayloadRejected):
            store.append(
                run_id=run_id,
                event_type="provider.debug",
                payload=payload,
                durability=EventDurability.EPHEMERAL,
            )
    finally:
        engine.dispose()


def test_repository_event_write_cannot_bypass_payload_policy() -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        with pytest.raises(EventPayloadRejected):
            repository.transition_run(
                run_id,
                RunEvent.DISPATCHED,
                payload={"api key": "secret"},
            )
        snapshot = store.get_run_snapshot(run_id)
        events = repository.list_events(run_id)
    finally:
        engine.dispose()

    assert snapshot.run.status == RunStatus.QUEUED.value
    assert snapshot.snapshot_seq == 1
    assert len(events) == 1


@pytest.mark.parametrize(
    "event_type",
    ["page.progress\nid: 999", "page.progress\revent: forged", "Page.Progress"],
)
def test_event_type_rejects_sse_directive_injection(event_type: str) -> None:
    repository, store, engine = create_stores()
    try:
        run_id = create_run(repository)
        with pytest.raises(ValueError, match="event_type"):
            store.append(
                run_id=run_id,
                event_type=event_type,
                payload={},
                durability=EventDurability.EPHEMERAL,
            )
    finally:
        engine.dispose()


def test_snapshot_uses_one_sqlite_read_transaction_during_concurrent_write() -> None:
    repository, store, engine = create_stores()
    first_read_finished = Event()
    writer_finished = Event()

    @event.listens_for(engine, "after_cursor_execute")
    def pause_snapshot_after_run_read(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            current_thread().name.startswith("snapshot-reader")
            and "FROM workflow_runs" in statement
            and not first_read_finished.is_set()
        ):
            first_read_finished.set()
            assert writer_finished.wait(timeout=5)

    try:
        run_id = create_run(repository)

        def add_step() -> None:
            assert first_read_finished.wait(timeout=5)
            repository.create_step(run_id, "plan", "snapshot-race-step")
            writer_finished.set()

        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="snapshot-reader"
        ) as executor:
            snapshot_future = executor.submit(store.get_run_snapshot, run_id)
            writer_future = executor.submit(add_step)
            snapshot = snapshot_future.result(timeout=10)
            writer_future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", pause_snapshot_after_run_read)
        engine.dispose()

    assert snapshot.snapshot_seq == 1
    assert snapshot.steps == ()
