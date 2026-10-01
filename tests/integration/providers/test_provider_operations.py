from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ppt_agent.domain.state_machine import AttemptStatus
from ppt_agent.ledger.costs import BudgetExceeded, CostLedger, ReservationStatus, UsageInput
from ppt_agent.operations.provider import (
    OperationAggregateStatus,
    OperationBusy,
    OperationConflict,
    PrepareAttempt,
    ProviderOperationService,
    RetryRiskConfirmationRequired,
)
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import (
    Base,
    ProjectRecord,
)


def create_services(
    hard_limit: int = 2_000,
) -> tuple[ProviderOperationService, CostLedger, object, str]:
    directory = Path(".cache/test-provider-operations")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(
        DatabaseConfig(path=directory / f"{uuid4()}.db", busy_timeout_ms=10_000)
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Provider test"))
    ledger = CostLedger(sessions)
    account = ledger.create_account(
        project_id, "page_generation", "CNY", hard_limit
    )
    return ProviderOperationService(sessions), ledger, engine, account.id


def attempt_request(
    account_id: str,
    *,
    operation_id: str = "operation-1",
    amount: int = 300,
) -> PrepareAttempt:
    return PrepareAttempt(
        operation_id=operation_id,
        operation_kind="image.generate",
        request_fingerprint="sha256:request-v1",
        provider="volcengine",
        model="seedream-configured-model",
        account_id=account_id,
        reservation_amount=amount,
        currency="CNY",
        pricing_version="seedream-v1",
        idempotency_key=None,
    )


def test_transaction_a_creates_operation_attempt_and_reservation_atomically() -> None:
    service, ledger, engine, account_id = create_services()
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))
        operation = service.get_operation(prepared.operation_id)
        reservation = ledger.get_reservation(prepared.reservation_id)
    finally:
        engine.dispose()

    assert prepared.status is AttemptStatus.PREPARED
    assert prepared.attempt_no == 1
    assert reservation.status is ReservationStatus.ACTIVE
    assert reservation.operation_id == operation.id
    assert operation.aggregate_status is OperationAggregateStatus.IN_PROGRESS


def test_provider_child_ownership_requires_a_run() -> None:
    service, _, engine, account_id = create_services()
    request = attempt_request(account_id)
    request = replace(request, step_id=str(uuid4()))
    try:
        with pytest.raises(OperationConflict, match="require run_id"):
            service.prepare_attempt(request)
    finally:
        engine.dispose()


def test_budget_failure_rolls_back_operation_attempt_and_reservation() -> None:
    service, _, engine, account_id = create_services(hard_limit=200)

    try:
        with pytest.raises(BudgetExceeded):
            service.prepare_attempt(attempt_request(account_id, amount=300))

        with engine.connect() as connection:
            counts = (
                connection.execute(text("SELECT count(*) FROM provider_operations")).scalar_one(),
                connection.execute(text("SELECT count(*) FROM provider_attempts")).scalar_one(),
                connection.execute(text("SELECT count(*) FROM cost_reservations")).scalar_one(),
            )
    finally:
        engine.dispose()

    assert counts == (0, 0, 0)


def test_transaction_b_compare_and_set_commits_submission_intent() -> None:
    service, _, engine, account_id = create_services()
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))

        submitted = service.commit_submission_intent(prepared.id)
        with pytest.raises(OperationBusy, match="prepared"):
            service.commit_submission_intent(prepared.id)
    finally:
        engine.dispose()

    assert submitted.status is AttemptStatus.SUBMITTED
    assert submitted.request_started_at is not None


def test_async_provider_job_id_is_persisted_on_attempt() -> None:
    service, _, engine, account_id = create_services()
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(prepared.id)

        running = service.record_provider_job_accepted(prepared.id, "remote-job-1")
        restored = service.get_attempt(prepared.id)
    finally:
        engine.dispose()

    assert running.status is AttemptStatus.PROVIDER_RUNNING
    assert restored.provider_job_id == "remote-job-1"


def test_known_uncharged_failure_releases_reservation() -> None:
    service, ledger, engine, account_id = create_services()
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(prepared.id)

        failed = service.record_known_failure(
            prepared.id,
            error_code="invalid_request",
            confirmed_uncharged=True,
        )
        operation = service.get_operation(prepared.operation_id)
        reservation = ledger.get_reservation(prepared.reservation_id)
        account = ledger.get_account(account_id)
    finally:
        engine.dispose()

    assert failed.status is AttemptStatus.FAILED
    assert failed.error_code == "invalid_request"
    assert operation.aggregate_status is OperationAggregateStatus.FAILED
    assert reservation.status is ReservationStatus.RELEASED
    assert account.active_reserved == 0


def test_unknown_outcome_requires_explicit_risk_confirmation_for_new_attempt() -> None:
    service, ledger, engine, account_id = create_services()
    try:
        first = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(first.id)
        service.begin_recovery(first.id)
        unknown = service.mark_outcome_unknown(first.id)

        with pytest.raises(RetryRiskConfirmationRequired):
            service.prepare_attempt(attempt_request(account_id))

        service.authorize_retry_after_unknown(
            operation_id=first.operation_id,
            unknown_attempt_id=first.id,
            acknowledgement="I accept the risk of duplicate billing.",
        )
        second = service.prepare_attempt(attempt_request(account_id))
        attempts = service.list_attempts(first.operation_id)
        account = ledger.get_account(account_id)
    finally:
        engine.dispose()

    assert unknown.status is AttemptStatus.OUTCOME_UNKNOWN
    assert second.attempt_no == 2
    assert [attempt.id for attempt in attempts] == [first.id, second.id]
    assert account.held_unknown == 300
    assert account.active_reserved == 300


def test_nonterminal_attempt_blocks_parallel_attempt() -> None:
    service, _, engine, account_id = create_services()
    try:
        service.prepare_attempt(attempt_request(account_id))
        with pytest.raises(OperationBusy, match="non-terminal"):
            service.prepare_attempt(attempt_request(account_id))
    finally:
        engine.dispose()


def test_recovered_cancel_requested_attempt_becomes_unknown_and_holds_budget() -> None:
    service, ledger, engine, account_id = create_services()
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(prepared.id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE provider_attempts SET status = 'cancel_requested' "
                    "WHERE id = :attempt_id"
                ),
                {"attempt_id": prepared.id},
            )

        cancelled = service.cancel_unresolved(prepared.id)
        reservation = ledger.get_reservation(prepared.reservation_id)
        account = ledger.get_account(account_id)
    finally:
        engine.dispose()

    assert cancelled.status is AttemptStatus.OUTCOME_UNKNOWN
    assert reservation.status is ReservationStatus.HELD_UNKNOWN
    assert account.held_unknown == 300


def test_known_uncharged_retry_persists_due_time() -> None:
    service, _, engine, account_id = create_services()
    due_at = datetime.now(UTC) + timedelta(minutes=1)
    try:
        prepared = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(prepared.id)
        service.record_known_failure(
            prepared.id,
            error_code="rate_limited",
            confirmed_uncharged=True,
            retry_at=due_at,
        )
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT reason, consumed_at FROM provider_retry_schedules "
                "WHERE attempt_id = :attempt_id"
            ), {"attempt_id": prepared.id}).one()
    finally:
        engine.dispose()

    assert row.reason == "rate_limited"
    assert row.consumed_at is None


def test_success_settles_attempt_and_selects_it_without_hiding_unknown_history() -> None:
    service, ledger, engine, account_id = create_services()
    try:
        first = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(first.id)
        service.begin_recovery(first.id)
        service.mark_outcome_unknown(first.id)
        service.authorize_retry_after_unknown(
            first.operation_id,
            first.id,
            "I accept the risk of duplicate billing.",
        )
        second = service.prepare_attempt(attempt_request(account_id))
        service.commit_submission_intent(second.id)

        succeeded = service.record_success(
            second.id,
            UsageInput(
                usage_key="usage-second",
                attempt_id=second.id,
                provider="volcengine",
                model="seedream-configured-model",
                estimated_amount=300,
                actual_amount=250,
                currency="CNY",
                pricing_version="seedream-v1",
                image_count=1,
            ),
        )
        operation = service.get_operation(first.operation_id)
        account = ledger.get_account(account_id)
    finally:
        engine.dispose()

    assert succeeded.status is AttemptStatus.SUCCEEDED
    assert operation.aggregate_status is OperationAggregateStatus.SUCCEEDED
    assert operation.selected_attempt_id == second.id
    assert operation.has_unknown_attempts is True
    assert account.settled == 250
    assert account.held_unknown == 300
    assert account.active_reserved == 0
