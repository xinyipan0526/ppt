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
    PageRecord,
    ProviderAttemptRecord,
    ProviderBinaryResultRecord,
    ProviderOperationRecord,
    ProviderRetryScheduleRecord,
    ProviderRetryAuthorizationRecord,
    ProviderTextResultRecord,
    ProviderVisionResultRecord,
    UsageRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
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
    run_id: str | None = None
    step_id: str | None = None
    page_id: str | None = None


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
    run_id: str | None
    step_id: str | None
    page_id: str | None


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


@dataclass(frozen=True, slots=True)
class StoredTextResult:
    operation_id: str
    attempt_id: str
    request_fingerprint: str
    schema_name: str
    schema_version: int
    output_payload: dict[str, Any]
    usage: dict[str, int]


@dataclass(frozen=True, slots=True)
class StoredBinaryResult:
    operation_id: str
    attempt_id: str
    request_fingerprint: str
    result_kind: str
    result_value: str
    media_type: str
    usage: dict[str, Any]
    download_status: str
    artifact_id: str | None


@dataclass(frozen=True, slots=True)
class StoredVisionResult:
    operation_id: str
    attempt_id: str
    request_fingerprint: str
    schema_name: str
    schema_version: int
    output_payload: dict[str, Any]
    usage: dict[str, int]


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
            self._validate_ownership(session, request)
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
                    run_id=request.run_id,
                    step_id=request.step_id,
                    page_id=request.page_id,
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

    def cancel_unresolved(self, attempt_id: str) -> AttemptSnapshot:
        """Close a locally cancelled attempt without claiming a remote outcome."""

        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            if attempt.status is AttemptStatus.PREPARED:
                attempt.status = transition_attempt(
                    attempt.status, AttemptEvent.SUBMISSION_ABORTED
                )
                attempt.error_code = "cancelled_before_submission"
                self._ledger.release_in_transaction(session, attempt.reservation_id)
            elif attempt.status in {
                AttemptStatus.SUBMITTED,
                AttemptStatus.PROVIDER_RUNNING,
                AttemptStatus.RECONCILING,
            }:
                attempt.status = transition_attempt(
                    attempt.status, AttemptEvent.CANCEL_REQUESTED
                )
                attempt.status = transition_attempt(
                    attempt.status, AttemptEvent.CANCEL_OUTCOME_UNKNOWN
                )
                attempt.error_code = "cancel_outcome_unknown"
                self._ledger.hold_unknown_in_transaction(
                    session, attempt.reservation_id
                )
            elif attempt.status is AttemptStatus.CANCEL_REQUESTED:
                attempt.status = transition_attempt(
                    attempt.status, AttemptEvent.CANCEL_OUTCOME_UNKNOWN
                )
                attempt.error_code = "cancel_outcome_unknown"
                self._ledger.hold_unknown_in_transaction(
                    session, attempt.reservation_id
                )
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def record_success(
        self,
        attempt_id: str,
        usage: UsageInput,
        response_summary: dict[str, Any] | None = None,
        *,
        schema_name: str | None = None,
        schema_version: int | None = None,
        output_payload: dict[str, Any] | None = None,
        text_usage: dict[str, int] | None = None,
        binary_result: dict[str, Any] | None = None,
        vision_result: dict[str, Any] | None = None,
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
            text_values = (schema_name, schema_version, output_payload, text_usage)
            families = sum(
                (any(value is not None for value in text_values), binary_result is not None, vision_result is not None)
            )
            if families > 1:
                raise OperationConflict("only one durable result family may be recorded")
            if any(value is not None for value in text_values):
                if not all(value is not None for value in text_values):
                    raise OperationConflict("durable text result fields must be complete")
                session.add(
                    ProviderTextResultRecord(
                        operation_id=operation.id,
                        attempt_id=attempt.id,
                        schema_name=schema_name,  # type: ignore[arg-type]
                        schema_version=schema_version,  # type: ignore[arg-type]
                        output_payload=dict(output_payload or {}),
                        usage=dict(text_usage or {}),
                    )
                )
                session.flush()
            elif binary_result is not None:
                session.add(
                    ProviderBinaryResultRecord(
                        operation_id=operation.id,
                        attempt_id=attempt.id,
                        request_fingerprint=operation.request_fingerprint,
                        result_kind=str(binary_result["result_kind"]),
                        result_value=str(binary_result["result_value"]),
                        media_type=str(binary_result["media_type"]),
                        usage=dict(binary_result["usage"]),
                        download_status=str(binary_result.get("download_status", "stored")),
                        artifact_id=binary_result.get("artifact_id"),
                    )
                )
                session.flush()
            elif vision_result is not None:
                session.add(
                    ProviderVisionResultRecord(
                        operation_id=operation.id,
                        attempt_id=attempt.id,
                        request_fingerprint=operation.request_fingerprint,
                        schema_name=str(vision_result["schema_name"]),
                        schema_version=int(vision_result["schema_version"]),
                        output_payload=dict(vision_result["output_payload"]),
                        usage=dict(vision_result["usage"]),
                    )
                )
                session.flush()
            operation.selected_attempt_id = attempt.id
            self._recompute_operation(session, operation.id)
            return self._attempt_snapshot(attempt)

    def get_text_result(self, operation_id: str) -> StoredTextResult | None:
        with session_scope(self._sessions) as session:
            operation = session.get(ProviderOperationRecord, operation_id)
            if operation is None or operation.selected_attempt_id is None:
                return None
            record = session.get(ProviderTextResultRecord, operation_id)
            if record is None or record.attempt_id != operation.selected_attempt_id:
                raise OperationConflict("selected text operation has no durable result")
            return StoredTextResult(
                operation_id=operation.id,
                attempt_id=record.attempt_id,
                request_fingerprint=operation.request_fingerprint,
                schema_name=record.schema_name,
                schema_version=record.schema_version,
                output_payload=dict(record.output_payload),
                usage={key: int(value) for key, value in record.usage.items()},
            )

    def get_binary_result(self, operation_id: str) -> StoredBinaryResult | None:
        with session_scope(self._sessions) as session:
            operation = session.get(ProviderOperationRecord, operation_id)
            if operation is None or operation.selected_attempt_id is None:
                return None
            record = session.get(ProviderBinaryResultRecord, operation_id)
            if record is None or record.attempt_id != operation.selected_attempt_id:
                raise OperationConflict("selected image operation has no durable result")
            return StoredBinaryResult(
                operation.id, record.attempt_id, record.request_fingerprint,
                record.result_kind, record.result_value, record.media_type,
                dict(record.usage),
                record.download_status, record.artifact_id,
            )

    def complete_binary_download(
        self,
        operation_id: str,
        *,
        attempt_id: str,
        encoded_content: str,
        media_type: str,
        dimensions: str,
    ) -> StoredBinaryResult:
        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            operation = self._require_operation(session, operation_id)
            record = session.get(ProviderBinaryResultRecord, operation_id)
            if (
                record is None
                or operation.selected_attempt_id != attempt_id
                or record.attempt_id != attempt_id
                or record.result_kind not in {"url", "base64"}
            ):
                raise OperationConflict("selected image result cannot be finalized")
            if record.result_kind == "base64":
                if record.result_value != encoded_content:
                    raise OperationConflict("image download was already finalized differently")
            else:
                record.result_kind = "base64"
                record.result_value = encoded_content
                record.media_type = media_type
                record.usage = {**dict(record.usage), "dimensions": dimensions}
                record.download_status = "stored"
            session.flush()
            return StoredBinaryResult(
                operation.id, record.attempt_id, record.request_fingerprint,
                record.result_kind, record.result_value, record.media_type,
                dict(record.usage), record.download_status, record.artifact_id,
            )

    def get_vision_result(self, operation_id: str) -> StoredVisionResult | None:
        with session_scope(self._sessions) as session:
            operation = session.get(ProviderOperationRecord, operation_id)
            if operation is None or operation.selected_attempt_id is None:
                return None
            record = session.get(ProviderVisionResultRecord, operation_id)
            if record is None or record.attempt_id != operation.selected_attempt_id:
                raise OperationConflict("selected vision operation has no durable result")
            return StoredVisionResult(
                operation.id, record.attempt_id, record.request_fingerprint,
                record.schema_name, record.schema_version,
                dict(record.output_payload),
                {key: int(value) for key, value in record.usage.items()},
            )

    def record_known_failure(
        self,
        attempt_id: str,
        *,
        error_code: str,
        confirmed_uncharged: bool,
        retry_at: datetime | None = None,
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
            if retry_at is not None:
                if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                    raise ValueError("retry_at must be timezone-aware")
                session.add(ProviderRetryScheduleRecord(
                    operation_id=attempt.operation_id,
                    attempt_id=attempt.id,
                    due_at=retry_at,
                    reason=error_code,
                ))
            self._recompute_operation(session, attempt.operation_id)
            return self._attempt_snapshot(attempt)

    def next_retry_due(self, run_id: str) -> datetime | None:
        with session_scope(self._sessions) as session:
            due = session.scalar(
                select(func.min(ProviderRetryScheduleRecord.due_at))
                .join(
                    ProviderOperationRecord,
                    ProviderOperationRecord.id == ProviderRetryScheduleRecord.operation_id,
                )
                .where(
                    ProviderOperationRecord.run_id == run_id,
                    ProviderRetryScheduleRecord.consumed_at.is_(None),
                )
            )
            if due is not None and (due.tzinfo is None or due.utcoffset() is None):
                due = due.replace(tzinfo=UTC)
            return due

    def record_charged_failure(
        self,
        attempt_id: str,
        usage: UsageInput,
        error_code: str,
        response_summary: dict[str, Any] | None = None,
    ) -> AttemptSnapshot:
        """Settle known billed usage while keeping the operation unsuccessful."""

        with session_scope(self._sessions) as session:
            self._ledger.begin_write_transaction(session)
            attempt = self._require_attempt(session, attempt_id)
            if usage.attempt_id != attempt.id:
                raise OperationConflict("usage attempt_id does not match attempt")
            attempt.status = transition_attempt(
                attempt.status, AttemptEvent.KNOWN_FAILURE
            )
            self._ledger.settle_in_transaction(session, attempt.reservation_id, usage)
            usage_record = session.scalar(
                select(UsageRecord).where(UsageRecord.usage_key == usage.usage_key)
            )
            if usage_record is None:
                raise RuntimeError("usage record was not persisted")
            attempt.usage_record_id = usage_record.id
            attempt.error_code = error_code
            attempt.response_received_at = datetime.now(UTC)
            attempt.response_summary = response_summary
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
    def _validate_ownership(session: Session, request: PrepareAttempt) -> None:
        if (request.step_id is not None or request.page_id is not None) and request.run_id is None:
            raise OperationConflict("step_id and page_id require run_id ownership")
        if request.run_id is None:
            return
        if session.get(WorkflowRunRecord, request.run_id) is None:
            raise OperationConflict("provider operation run does not exist")
        if request.step_id is not None:
            step = session.get(WorkflowStepRecord, request.step_id)
            if step is None or step.run_id != request.run_id:
                raise OperationConflict("provider operation step does not belong to run")
        if request.page_id is not None:
            page = session.get(PageRecord, request.page_id)
            if page is None or page.run_id != request.run_id:
                raise OperationConflict("provider operation page does not belong to run")

    @staticmethod
    def _validate_operation_identity(
        operation: ProviderOperationRecord, request: PrepareAttempt
    ) -> None:
        expected = (
            request.operation_kind,
            request.request_fingerprint,
            request.provider,
            request.model,
            request.run_id,
            request.step_id,
            request.page_id,
        )
        actual = (
            operation.operation_kind,
            operation.request_fingerprint,
            operation.provider,
            operation.model,
            operation.run_id,
            operation.step_id,
            operation.page_id,
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
            run_id=record.run_id,
            step_id=record.step_id,
            page_id=record.page_id,
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
