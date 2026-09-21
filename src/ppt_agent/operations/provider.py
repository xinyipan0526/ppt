"""Two-transaction provider submission and recovery protocol."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ppt_agent.domain.providers import OperationAggregateStatus
from ppt_agent.domain.state_machine import (
    AttemptEvent,
    AttemptStatus,
    transition_attempt,
)
from ppt_agent.ledger.costs import CostLedger, UsageInput
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ProviderAttemptRecord,
    ProviderOperationRecord,
    ProviderRetryAuthorizationRecord,
    UsageRecord,
)


class ProviderOperationError(RuntimeError):
    """Base class for deterministic provider operation failures."""


class OperationBusy(ProviderOperationError):
    """Raised when another attempt is still unresolved."""


class OperationConflict(ProviderOperationError):
    """Raised when a stable operation identity is reused inconsistently."""


class RetryRiskConfirmationRequired(ProviderOperationError):
    """Raised when an unknown attempt requires explicit user authorization."""


class ProviderRecordNotFound(ProviderOperationError):
    """Raised when an operation or attempt does not exist."""


@dataclass(frozen=True, slots=True)
class PrepareAttempt:
    operation_id: str
    operation_kind: str
    request_fingerprint: str
    provider: str
    model: str
    account_id: str
    reservation_amount: int
    currency: str
    pricing_version: str
    idempotency_key: str | None


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    id: str
    operation_kind: str
    request_fingerprint: str
    provider: str
    model: str
    aggregate_status: OperationAggregateStatus
    selected_attempt_id: str | None
    has_unknown_attempts: bool


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    id: str
    operation_id: str
    attempt_no: int
    status: AttemptStatus
    idempotency_key: str | None
    provider_job_id: str | None
    reservation_id: str
    request_started_at: datetime | None
    response_received_at: datetime | None
    usage_record_id: str | None
    error_code: str | None
    response_summary: dict[str, Any] | None


NONTERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.PREPARED,
    AttemptStatus.SUBMITTED,
    AttemptStatus.PROVIDER_RUNNING,
    AttemptStatus.RECONCILING,
    AttemptStatus.CANCEL_REQUESTED,
}


class ProviderOperationService:
    """Persist provider intent separately from every real submission attempt."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._ledger = CostLedger(sessions)

    def prepare_attempt(self, request: PrepareAttempt) -> AttemptSnapshot:
        """Transaction A: create prepared Attempt and Reservation atomically."""

        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            operation = session.get(ProviderOperationRecord, request.operation_id)
            if operation is None:
                operation = ProviderOperationRecord(
                    id=request.operation_id,
                    operation_kind=request.operation_kind,
                    request_fingerprint=request.request_fingerprint,
                    provider=request.provider,
                    model=request.model,
                    aggregate_status=OperationAggregateStatus.PENDING,
                    selected_attempt_id=None,
                    has_unknown_attempts=False,
                )
                session.add(operation)
                session.flush()
            else:
                self._validate_operation_identity(operation, request)

            attempts = self._attempt_records(session, operation.id)
            if any(attempt.status in NONTERMINAL_ATTEMPT_STATUSES for attempt in attempts):
                raise OperationBusy("operation already has a non-terminal attempt")
            if operation.selected_attempt_id is not None:
                raise OperationConflict("successful operation cannot create another attempt")

            authorization = None
            unknown_attempts = [
                attempt
                for attempt in attempts
                if attempt.status is AttemptStatus.OUTCOME_UNKNOWN
            ]
            if unknown_attempts:
                authorization = session.scalar(
                    select(ProviderRetryAuthorizationRecord)
                    .where(
                        ProviderRetryAuthorizationRecord.operation_id == operation.id,
                        ProviderRetryAuthorizationRecord.unknown_attempt_id.in_(
                            [attempt.id for attempt in unknown_attempts]
                        ),
                        ProviderRetryAuthorizationRecord.consumed_by_attempt_id.is_(None),
                    )
                    .order_by(ProviderRetryAuthorizationRecord.created_at.desc())
                )
                if authorization is None:
                    raise RetryRiskConfirmationRequired(
                        "unknown attempt requires explicit duplicate billing confirmation"
                    )

            attempt_id = str(uuid4())
            next_attempt_no = max((attempt.attempt_no for attempt in attempts), default=0) + 1
            reservation = self._ledger.reserve_in_transaction(
                session,
                account_id=request.account_id,
                reservation_key=attempt_id,
                operation_id=operation.id,
                amount=request.reservation_amount,
                currency=request.currency,
                pricing_version=request.pricing_version,
            )
            attempt = ProviderAttemptRecord(
                id=attempt_id,
                operation_id=operation.id,
                attempt_no=next_attempt_no,
                status=AttemptStatus.PREPARED,
                idempotency_key=request.idempotency_key,
                reservation_id=reservation.id,
            )
            session.add(attempt)
            if authorization is not None:
                authorization.consumed_by_attempt_id = attempt_id
            operation.aggregate_status = OperationAggregateStatus.IN_PROGRESS
            operation.has_unknown_attempts = bool(unknown_attempts)
            session.flush()
            return self._attempt_snapshot(attempt)

    def commit_submission_intent(self, attempt_id: str) -> AttemptSnapshot:
        """Transaction B: CAS prepared to submitted before any network I/O."""

        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            started_at = datetime.now(UTC)
            result = session.execute(
                update(ProviderAttemptRecord)
                .where(
                    ProviderAttemptRecord.id == attempt_id,
                    ProviderAttemptRecord.status == AttemptStatus.PREPARED,
                )
                .values(
                    status=AttemptStatus.SUBMITTED,
                    request_started_at=started_at,
                )
            )
            if result.rowcount != 1:
                existing = session.get(ProviderAttemptRecord, attempt_id)
                if existing is None:
                    raise ProviderRecordNotFound(f"attempt not found: {attempt_id}")
                raise OperationBusy(
                    f"attempt must be prepared, current state is {existing.status.value}"
                )
            attempt = self._require_attempt(session, attempt_id)
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def record_provider_job_accepted(
        self, attempt_id: str, provider_job_id: str
    ) -> AttemptSnapshot:
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.PROVIDER_JOB_ACCEPTED
            )
            attempt.provider_job_id = provider_job_id
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def begin_recovery(self, attempt_id: str) -> AttemptSnapshot:
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.RECOVERY_STARTED
            )
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def mark_outcome_unknown(self, attempt_id: str) -> AttemptSnapshot:
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.OUTCOME_CANNOT_BE_PROVEN
            )
            self._ledger.hold_unknown_in_transaction(session, attempt.reservation_id)
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def record_success(
        self,
        attempt_id: str,
        usage: UsageInput,
        response_summary: dict[str, Any] | None = None,
    ) -> AttemptSnapshot:
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            if usage.attempt_id != attempt.id:
                raise OperationConflict("usage attempt_id does not match attempt")
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.RESULT_RECEIVED
            )
            self._ledger.settle_in_transaction(session, attempt.reservation_id, usage)
            usage_record = session.scalar(
                select(UsageRecord).where(UsageRecord.usage_key == usage.usage_key)
            )
            if usage_record is None:
                raise RuntimeError("usage record was not persisted")
            attempt.usage_record_id = usage_record.id
            attempt.response_received_at = datetime.now(UTC)
            attempt.response_summary = response_summary
            operation = self._require_operation(session, attempt.operation_id)
            operation.selected_attempt_id = attempt.id
            self._recompute_operation(session, operation.id)
            return self._attempt_snapshot(attempt)

    def record_known_failure(
        self,
        attempt_id: str,
        *,
        error_code: str,
        confirmed_uncharged: bool,
    ) -> AttemptSnapshot:
        if not confirmed_uncharged:
            raise OperationConflict(
                "charged or uncertain failures require usage settlement or reconciliation"
            )
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.KNOWN_FAILURE
            )
            attempt.error_code = error_code
            attempt.response_received_at = datetime.now(UTC)
            self._ledger.release_in_transaction(session, attempt.reservation_id)
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def authorize_retry_after_unknown(
        self,
        operation_id: str,
        unknown_attempt_id: str,
        acknowledgement: str,
    ) -> str:
        if not acknowledgement.strip():
            raise ValueError("acknowledgement must not be empty")
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            operation = self._require_operation(session, operation_id)
            attempt = self._require_attempt(session, unknown_attempt_id)
            if attempt.operation_id != operation.id:
                raise OperationConflict("attempt does not belong to operation")
            if attempt.status is not AttemptStatus.OUTCOME_UNKNOWN:
                raise OperationConflict("attempt outcome is not unknown")
            existing = session.scalar(
                select(ProviderRetryAuthorizationRecord).where(
                    ProviderRetryAuthorizationRecord.operation_id == operation_id,
                    ProviderRetryAuthorizationRecord.unknown_attempt_id
                    == unknown_attempt_id,
                )
            )
            if existing is not None:
                if existing.acknowledgement != acknowledgement:
                    raise OperationConflict(
                        "retry authorization already records different acknowledgement"
                    )
                return existing.id
            record = ProviderRetryAuthorizationRecord(
                id=str(uuid4()),
                operation_id=operation_id,
                unknown_attempt_id=unknown_attempt_id,
                acknowledgement=acknowledgement,
            )
            session.add(record)
            return record.id

    def get_operation(self, operation_id: str) -> OperationSnapshot:
        with session_scope(self._sessions) as session:
            return self._operation_snapshot(
                self._require_operation(session, operation_id)
            )

    def get_attempt(self, attempt_id: str) -> AttemptSnapshot:
        with session_scope(self._sessions) as session:
            return self._attempt_snapshot(self._require_attempt(session, attempt_id))

    def list_attempts(self, operation_id: str) -> list[AttemptSnapshot]:
        with session_scope(self._sessions) as session:
            self._require_operation(session, operation_id)
            return [
                self._attempt_snapshot(record)
                for record in self._attempt_records(session, operation_id)
            ]

    @staticmethod
    def _validate_operation_identity(
        operation: ProviderOperationRecord, request: PrepareAttempt
    ) -> None:
        expected = (
            request.operation_kind,
            request.request_fingerprint,
            request.provider,
            request.model,
        )
        actual = (
            operation.operation_kind,
            operation.request_fingerprint,
            operation.provider,
            operation.model,
        )
        if actual != expected:
            raise OperationConflict(
                "operation identity changed; create a new operation_id"
            )

    @staticmethod
    def _attempt_records(
        session: Session, operation_id: str
    ) -> list[ProviderAttemptRecord]:
        return list(
            session.scalars(
                select(ProviderAttemptRecord)
                .where(ProviderAttemptRecord.operation_id == operation_id)
                .order_by(ProviderAttemptRecord.attempt_no)
            )
        )

    @classmethod
    def _recompute_operation(cls, session: Session, operation_id: str) -> None:
        operation = cls._require_operation(session, operation_id)
        attempts = cls._attempt_records(session, operation_id)
        operation.has_unknown_attempts = any(
            attempt.status is AttemptStatus.OUTCOME_UNKNOWN for attempt in attempts
        )
        if operation.selected_attempt_id is not None or any(
            attempt.status is AttemptStatus.SUCCEEDED for attempt in attempts
        ):
            operation.aggregate_status = OperationAggregateStatus.SUCCEEDED
        elif any(
            attempt.status in NONTERMINAL_ATTEMPT_STATUSES for attempt in attempts
        ):
            operation.aggregate_status = OperationAggregateStatus.IN_PROGRESS
        elif operation.has_unknown_attempts:
            operation.aggregate_status = OperationAggregateStatus.OUTCOME_UNKNOWN
        elif attempts and all(
            attempt.status is AttemptStatus.CANCELLED for attempt in attempts
        ):
            operation.aggregate_status = OperationAggregateStatus.CANCELLED
        elif attempts:
            operation.aggregate_status = OperationAggregateStatus.FAILED
        else:
            operation.aggregate_status = OperationAggregateStatus.PENDING

    @staticmethod
    def _require_operation(
        session: Session, operation_id: str
    ) -> ProviderOperationRecord:
        record = session.get(ProviderOperationRecord, operation_id)
        if record is None:
            raise ProviderRecordNotFound(f"operation not found: {operation_id}")
        return record

    @staticmethod
    def _require_attempt(session: Session, attempt_id: str) -> ProviderAttemptRecord:
        record = session.get(ProviderAttemptRecord, attempt_id)
        if record is None:
            raise ProviderRecordNotFound(f"attempt not found: {attempt_id}")
        return record

    @staticmethod
    def _operation_snapshot(record: ProviderOperationRecord) -> OperationSnapshot:
        return OperationSnapshot(
            id=record.id,
            operation_kind=record.operation_kind,
            request_fingerprint=record.request_fingerprint,
            provider=record.provider,
            model=record.model,
            aggregate_status=record.aggregate_status,
            selected_attempt_id=record.selected_attempt_id,
            has_unknown_attempts=record.has_unknown_attempts,
        )

    @staticmethod
    def _attempt_snapshot(record: ProviderAttemptRecord) -> AttemptSnapshot:
        return AttemptSnapshot(
            id=record.id,
            operation_id=record.operation_id,
            attempt_no=record.attempt_no,
            status=record.status,
            idempotency_key=record.idempotency_key,
            provider_job_id=record.provider_job_id,
            reservation_id=record.reservation_id,
            request_started_at=record.request_started_at,
            response_received_at=record.response_received_at,
            usage_record_id=record.usage_record_id,
            error_code=record.error_code,
            response_summary=record.response_summary,
        )

