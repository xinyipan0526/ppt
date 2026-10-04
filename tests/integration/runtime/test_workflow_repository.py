from pathlib import Path
from uuid import uuid4

import json

import pytest

from ppt_agent.domain.state_machine import (
    InvalidTransition,
    RunEvent,
    RunStatus,
    StepEvent,
)
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.models import Base


def create_repository() -> tuple[WorkflowRepository, object]:
    directory = Path(".cache/test-runtime")
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / f"{uuid4()}.db"
    engine = create_sqlite_engine(DatabaseConfig(path=database_path))
    Base.metadata.create_all(engine)
    return WorkflowRepository(create_session_factory(engine)), engine


def test_create_run_persists_initial_state_and_event() -> None:
    repository, engine = create_repository()
    try:
        project_id = repository.create_project("Annual report")
        run = repository.create_run(
            project_id=project_id,
            workflow_name="deck_generation",
            workflow_version=1,
        )

        events = repository.list_events(run.id)
    finally:
        engine.dispose()

    assert run.status is RunStatus.QUEUED
    assert [(event.run_seq, event.event_type) for event in events] == [
        (1, "run.queued")
    ]


def test_transition_updates_state_and_appends_next_event_atomically() -> None:
    repository, engine = create_repository()
    try:
        project_id = repository.create_project("Annual report")
        run = repository.create_run(project_id, "deck_generation", 1)

        transitioned = repository.transition_run(run.id, RunEvent.DISPATCHED)
        events = repository.list_events(run.id)
    finally:
        engine.dispose()

    assert transitioned.status is RunStatus.RUNNING
    assert [(event.run_seq, event.event_type) for event in events] == [
        (1, "run.queued"),
        (2, "run.run_dispatched"),
    ]


def test_invalid_transition_leaves_state_and_event_log_unchanged() -> None:
    repository, engine = create_repository()
    try:
        project_id = repository.create_project("Annual report")
        run = repository.create_run(project_id, "deck_generation", 1)

        with pytest.raises(InvalidTransition):
            repository.transition_run(
                run.id,
                RunEvent.ALL_REQUIRED_STEPS_SUCCEEDED,
            )

        unchanged = repository.get_run(run.id)
        events = repository.list_events(run.id)
    finally:
        engine.dispose()

    assert unchanged.status is RunStatus.QUEUED
    assert len(events) == 1


def test_failure_error_is_sanitized_before_step_run_and_event_persistence() -> None:
    repository, engine = create_repository()
    try:
        project_id = repository.create_project("Annual report")
        run = repository.create_run(project_id, "deck_generation", 1)
        step = repository.create_step(run.id, "build_brief", "build-brief-1")
        repository.transition_run(run.id, RunEvent.DISPATCHED)
        repository.transition_step(step.id, StepEvent.STARTED)
        unsafe = {
            "code": "authorization=Bearer secret",
            "provider_summary": {
                "http_status": 400,
                "provider_error_type": "api_key=secret",
                "exception_type": "CredentialException",
                "prompt": "private source text",
            },
        }
        repository.transition_step(step.id, StepEvent.FAILED, payload={"error": unsafe})
        repository.transition_run(
            run.id, RunEvent.UNRECOVERABLE_FAILURE, payload={"error": unsafe}
        )
        persisted_run = repository.get_run(run.id)
        events = repository.list_events(run.id)
    finally:
        engine.dispose()

    expected = {"code": "unknown_provider_error", "provider_summary": {"http_status": 400}}
    assert persisted_run.error == expected
    assert events[-2].payload["error"] == expected
    assert events[-1].payload["error"] == expected
    assert "secret" not in json.dumps([event.payload for event in events])
