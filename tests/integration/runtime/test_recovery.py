from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ppt_agent.domain.state_machine import AttemptStatus, RunStatus, StepStatus
from ppt_agent.runtime.recovery import RecoveryCoordinator
from ppt_agent.runtime.repository import WorkflowRepository
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, ProjectRecord, ProviderAttemptRecord, ProviderOperationRecord, WorkflowRunRecord, WorkflowStepRecord


def test_recovery_reconciles_remote_attempt_before_blocking_its_run() -> None:
    directory = Path(".cache/test-recovery")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id, run_id, step_id, operation_id = (str(uuid4()) for _ in range(4))
    try:
        with session_scope(sessions) as session:
            session.add(ProjectRecord(id=project_id, name="Recovery"))
            session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck", workflow_version=1, status=RunStatus.RUNNING))
            session.add(WorkflowStepRecord(id=step_id, run_id=run_id, step_key="generate", generation=1, status=StepStatus.RUNNING, attempt_count=1, idempotency_key=str(uuid4())))
            session.add(ProviderOperationRecord(id=operation_id, operation_kind="image.generate", request_fingerprint="sha256:x", provider="p", model="m", aggregate_status="in_progress", selected_attempt_id=None, has_unknown_attempts=False, run_id=run_id, step_id=step_id))
            session.add(ProviderAttemptRecord(id=str(uuid4()), operation_id=operation_id, attempt_no=1, status=AttemptStatus.SUBMITTED, reservation_id="reservation-placeholder"))
            # This focused test bypasses ledger creation, so disable FK checks for fixture insertion.
            session.connection().exec_driver_sql("PRAGMA foreign_keys=OFF")

        plan = RecoveryCoordinator(sessions).recover()
        with session_scope(sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            step = session.get(WorkflowStepRecord, step_id)
            attempt = session.scalar(__import__("sqlalchemy").select(ProviderAttemptRecord))
    finally:
        engine.dispose()

    assert plan.blocked_run_ids == (run_id,)
    assert plan.queued_run_ids == ()
    assert run.status is RunStatus.RECOVERY_REQUIRED
    assert step.status is StepStatus.RECOVERY_REQUIRED
    assert attempt.status is AttemptStatus.RECONCILING


def test_recovery_blocks_unproven_restart_and_queues_due_retry_idempotently() -> None:
    directory = Path(".cache/test-recovery")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id, running_id, retry_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC)
    try:
        with session_scope(sessions) as session:
            session.add(ProjectRecord(id=project_id, name="Recovery"))
            session.add(WorkflowRunRecord(id=running_id, project_id=project_id, workflow_name="deck", workflow_version=1, status=RunStatus.RUNNING))
            session.add(WorkflowRunRecord(id=retry_id, project_id=project_id, workflow_name="deck", workflow_version=1, status=RunStatus.WAITING_FOR_RETRY, retry_at=now - timedelta(seconds=1)))

        first = RecoveryCoordinator(sessions).recover(now=now)
        second = RecoveryCoordinator(sessions).recover(now=now)
    finally:
        engine.dispose()

    assert first.queued_run_ids == (retry_id,)
    assert first.blocked_run_ids == (running_id,)
    assert second.queued_run_ids == first.queued_run_ids
    assert second.reconciling_attempt_ids == ()


def test_recovery_does_not_replay_interrupted_local_step_without_proof() -> None:
    directory = Path(".cache/test-recovery")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id, run_id, step_id = (str(uuid4()) for _ in range(3))
    try:
        with session_scope(sessions) as session:
            session.add(ProjectRecord(id=project_id, name="Recovery"))
            session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck", workflow_version=1, status=RunStatus.RUNNING))
            session.add(WorkflowStepRecord(id=step_id, run_id=run_id, step_key="render", generation=1, status=StepStatus.RUNNING, attempt_count=1, idempotency_key=str(uuid4())))

        plan = RecoveryCoordinator(sessions).recover()
        retried = WorkflowRepository(sessions).mark_recovery_retry_safe(
            run_id, evidence="artifact reconciliation found no committed output"
        )
        with session_scope(sessions) as session:
            recovered_step = session.get(WorkflowStepRecord, step_id)
    finally:
        engine.dispose()

    assert plan.blocked_run_ids == (run_id,)
    assert plan.queued_run_ids == ()
    assert retried.status is RunStatus.QUEUED
    assert recovered_step.status is StepStatus.PENDING
    assert "retry_safe_evidence" in recovered_step.recovery_metadata
