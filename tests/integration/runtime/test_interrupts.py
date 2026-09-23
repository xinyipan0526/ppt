from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.domain.state_machine import RunEvent, RunStatus, StepEvent, StepStatus
from ppt_agent.runtime.repository import InterruptConflict, WorkflowRepository
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.models import Base, InterruptStatus
from ppt_agent.tools.clarification import ClarificationValidationError


def clarification_form() -> dict[str, object]:
    return {
        "schema_name": "clarification_form",
        "schema_version": 1,
        "kind": "clarification",
        "reason": "Need page count",
        "fields": [
            {
                "name": "page_count",
                "type": "number",
                "question": "How many pages?",
                "reason": "Planning requires a size.",
                "options": [],
                "recommended_value": 8,
                "required": True,
            }
        ],
        "context": {},
    }


def create_running_step() -> tuple[WorkflowRepository, object, str, str]:
    directory = Path(".cache/test-interrupts")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(
        DatabaseConfig(path=directory / f"{uuid4()}.db")
    )
    Base.metadata.create_all(engine)
    repository = WorkflowRepository(create_session_factory(engine))
    project_id = repository.create_project("Planning")
    run = repository.create_run(project_id, "deck_generation", 1)
    step = repository.create_step(run.id, "clarify_brief", "clarify-brief-1")
    repository.transition_run(run.id, RunEvent.DISPATCHED)
    repository.transition_step(step.id, StepEvent.STARTED)
    return repository, engine, run.id, step.id


def test_create_interrupt_moves_run_and_step_to_waiting_in_one_transaction() -> None:
    repository, engine, run_id, step_id = create_running_step()
    try:
        interrupt = repository.create_interrupt(
            run_id=run_id,
            step_id=step_id,
            form_schema=clarification_form(),
        )
        run = repository.get_run(run_id)
        step = repository.get_step(step_id)
    finally:
        engine.dispose()

    assert interrupt.status is InterruptStatus.PENDING
    assert run.status is RunStatus.WAITING_FOR_USER
    assert step.status is StepStatus.WAITING_FOR_USER


def test_resume_interrupt_requeues_run_and_step_with_new_generation() -> None:
    repository, engine, run_id, step_id = create_running_step()
    try:
        interrupt = repository.create_interrupt(
            run_id=run_id,
            step_id=step_id,
            form_schema=clarification_form(),
        )

        resumed = repository.resume_interrupt(
            interrupt.id,
            expected_version=1,
            payload={"page_count": 8},
        )
        run = repository.get_run(run_id)
        step = repository.get_step(step_id)
        events = repository.list_events(run_id)
    finally:
        engine.dispose()

    assert resumed.status is InterruptStatus.RESOLVED
    assert resumed.resume_payload == {"page_count": 8}
    assert run.status is RunStatus.QUEUED
    assert step.status is StepStatus.PENDING
    assert step.generation == 2
    assert events[-1].event_type == "interrupt.resumed"


def test_repeating_same_resume_is_idempotent_but_changed_payload_conflicts() -> None:
    repository, engine, run_id, step_id = create_running_step()
    try:
        interrupt = repository.create_interrupt(
            run_id=run_id,
            step_id=step_id,
            form_schema=clarification_form(),
        )
        first = repository.resume_interrupt(
            interrupt.id,
            expected_version=1,
            payload={"page_count": 8},
        )
        event_count = len(repository.list_events(run_id))

        repeated = repository.resume_interrupt(
            interrupt.id,
            expected_version=1,
            payload={"page_count": 8},
        )
        with pytest.raises(InterruptConflict, match="different payload"):
            repository.resume_interrupt(
                interrupt.id,
                expected_version=1,
                payload={"page_count": 10},
            )
        final_event_count = len(repository.list_events(run_id))
    finally:
        engine.dispose()

    assert repeated == first
    assert final_event_count == event_count


def test_resume_with_wrong_version_leaves_interrupt_pending() -> None:
    repository, engine, run_id, step_id = create_running_step()
    try:
        interrupt = repository.create_interrupt(
            run_id=run_id,
            step_id=step_id,
            form_schema=clarification_form(),
        )

        with pytest.raises(InterruptConflict, match="version mismatch"):
            repository.resume_interrupt(
                interrupt.id,
                expected_version=2,
                payload={"page_count": 8},
            )
        unchanged = repository.get_interrupt(interrupt.id)
    finally:
        engine.dispose()

    assert unchanged.status is InterruptStatus.PENDING
    assert unchanged.resume_payload is None


def test_invalid_resume_payload_is_rejected_without_mutating_state_or_events() -> None:
    repository, engine, run_id, step_id = create_running_step()
    try:
        interrupt = repository.create_interrupt(
            run_id=run_id,
            step_id=step_id,
            form_schema=clarification_form(),
        )
        before_events = len(repository.list_events(run_id))

        with pytest.raises(ClarificationValidationError, match="unknown answer"):
            repository.resume_interrupt(
                interrupt.id,
                expected_version=1,
                payload={"page_count": 8, "api_key": "must-not-be-accepted"},
            )

        unchanged_interrupt = repository.get_interrupt(interrupt.id)
        unchanged_run = repository.get_run(run_id)
        unchanged_step = repository.get_step(step_id)
        after_events = len(repository.list_events(run_id))
    finally:
        engine.dispose()

    assert unchanged_interrupt.status is InterruptStatus.PENDING
    assert unchanged_run.status is RunStatus.WAITING_FOR_USER
    assert unchanged_step.status is StepStatus.WAITING_FOR_USER
    assert unchanged_step.generation == 1
    assert after_events == before_events
