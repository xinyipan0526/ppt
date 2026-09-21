"""Atomic, integer-valued budget ledger for paid provider operations."""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ppt_agent.domain.costs import ReservationStatus
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    BudgetAccountRecord,
    CostReservationRecord,
    UsageRecord,
)


class LedgerError(RuntimeError):
    """Base class for deterministic ledger failures."""


class BudgetExceeded(LedgerError):
    """Raised when a reservation would exceed the hard budget."""


class CurrencyMismatch(LedgerError):
    """Raised when amounts with different currencies are combined."""


class ReservationConflict(LedgerError):
    """Raised when an idempotency key is reused with different inputs."""


class LedgerRecordNotFound(LedgerError):
    """Raised when an account or reservation cannot be found."""


@dataclass(frozen=True, slots=True)
class UsageInput:
    usage_key: str
    attempt_id: str
    provider: str
    model: str
    estimated_amount: int
    actual_amount: int
    currency: str
    pricing_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0
    dimensions: str | None = None
    raw_units: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "estimated_amount",
            "actual_amount",
            "input_tokens",
            "output_tokens",
            "image_count",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        _validate_currency(self.currency)


@dataclass(frozen=True, slots=True)
class BudgetAccountSnapshot:
    id: str
    project_id: str
    stage: str
    currency: str
    hard_limit: int
    soft_limit: int | None
    settled: int
    active_reserved: int
    held_unknown: int

    @property
    def available(self) -> int:
        return self.hard_limit - self.settled - self.active_reserved - self.held_unknown


@dataclass(frozen=True, slots=True)
class ReservationSnapshot:
    id: str
    account_id: str
    reservation_key: str
    operation_id: str
    amount_reserved: int
    amount_settled: int
    budget_deviation: int
    currency: str
    pricing_version: str
    status: ReservationStatus


class CostLedger:
    """Serialize budget writes in short SQLite transactions."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def create_account(
        self,
        project_id: str,
        stage: str,
        currency: str,
        hard_limit: int,
        soft_limit: int | None = None,
    ) -> BudgetAccountSnapshot:
        _validate_currency(currency)
        if hard_limit < 0:
            raise ValueError("hard_limit must be non-negative")
        if soft_limit is not None and not 0 <= soft_limit <= hard_limit:
            raise ValueError("soft_limit must be between zero and hard_limit")

        with session_scope(self._sessions) as session:
            self.begin_write_transaction(session)
            record = BudgetAccountRecord(
                id=str(uuid4()),
                project_id=project_id,
                stage=stage,
                currency=currency,
                hard_limit=hard_limit,
                soft_limit=soft_limit,
                settled=0,
                active_reserved=0,
                held_unknown=0,
            )
            session.add(record)
            session.flush()
            return self._account_snapshot(record)

    def get_account(self, account_id: str) -> BudgetAccountSnapshot:
        with session_scope(self._sessions) as session:
            return self._account_snapshot(self._require_account(session, account_id))

    def reserve(
        self,
        account_id: str,
        operation_id: str,
        amount: int,
        currency: str,
        pricing_version: str,
    ) -> ReservationSnapshot:
        _validate_currency(currency)
        if amount <= 0:
            raise ValueError("reservation amount must be positive")

        with session_scope(self._sessions) as session:
            self.begin_write_transaction(session)
            return self.reserve_in_transaction(
                session,
                account_id=account_id,
                reservation_key=operation_id,
                operation_id=operation_id,
                amount=amount,
                currency=currency,
                pricing_version=pricing_version,
            )

    def reserve_in_transaction(
        self,
        session: Session,
        *,
        account_id: str,
        reservation_key: str,
        operation_id: str,
        amount: int,
        currency: str,
        pricing_version: str,
    ) -> ReservationSnapshot:
        """Reserve funds inside a caller-owned immediate write transaction."""

        _validate_currency(currency)
        if amount <= 0:
            raise ValueError("reservation amount must be positive")
        existing = session.scalar(
            select(CostReservationRecord).where(
                CostReservationRecord.reservation_key == reservation_key
            )
        )
        if existing is not None:
            if (
                existing.account_id != account_id
                or existing.operation_id != operation_id
                or existing.amount_reserved != amount
                or existing.currency != currency
                or existing.pricing_version != pricing_version
            ):
                raise ReservationConflict(
                    "reservation key already has different parameters"
                )
            return self._reservation_snapshot(existing)

        account = self._require_account(session, account_id)
        if account.currency != currency:
            raise CurrencyMismatch(
                f"account uses {account.currency}, reservation uses {currency}"
            )
        committed = account.settled + account.active_reserved + account.held_unknown
        if committed + amount > account.hard_limit:
            raise BudgetExceeded(
                f"reservation would exceed hard limit by "
                f"{committed + amount - account.hard_limit} {currency} minor units"
            )

        account.active_reserved += amount
        record = CostReservationRecord(
            id=str(uuid4()),
            account_id=account.id,
            project_id=account.project_id,
            stage=account.stage,
            reservation_key=reservation_key,
            operation_id=operation_id,
            amount_reserved=amount,
            amount_settled=0,
            budget_deviation=0,
            currency=currency,
            pricing_version=pricing_version,
            status=ReservationStatus.ACTIVE,
        )
        session.add(record)
        session.flush()
        return self._reservation_snapshot(record)

    def get_reservation(self, reservation_id: str) -> ReservationSnapshot:
        with session_scope(self._sessions) as session:
            return self._reservation_snapshot(
                self._require_reservation(session, reservation_id)
            )

    def settle(
        self,
        reservation_id: str,
        usage: UsageInput,
    ) -> ReservationSnapshot:
        with session_scope(self._sessions) as session:
            self.begin_write_transaction(session)
            return self.settle_in_transaction(session, reservation_id, usage)

    def settle_in_transaction(
        self,
        session: Session,
        reservation_id: str,
        usage: UsageInput,
    ) -> ReservationSnapshot:
        existing_usage = session.scalar(
            select(UsageRecord).where(UsageRecord.usage_key == usage.usage_key)
        )
        if existing_usage is not None:
            if not self._usage_matches(existing_usage, reservation_id, usage):
                raise ReservationConflict(
                    "usage key already recorded with different values"
                )
            return self._reservation_snapshot(
                self._require_reservation(session, reservation_id)
            )

        reservation = self._require_reservation(session, reservation_id)
        if reservation.currency != usage.currency:
            raise CurrencyMismatch(
                f"reservation uses {reservation.currency}, usage uses {usage.currency}"
            )
        if reservation.pricing_version != usage.pricing_version:
            raise ReservationConflict("usage pricing version does not match reservation")
        if reservation.status not in {
            ReservationStatus.ACTIVE,
            ReservationStatus.HELD_UNKNOWN,
        }:
            raise ReservationConflict(
                f"cannot settle reservation in {reservation.status.value}"
            )

        account = self._require_account(session, reservation.account_id)
        if reservation.status is ReservationStatus.ACTIVE:
            account.active_reserved -= reservation.amount_reserved
        else:
            account.held_unknown -= reservation.amount_reserved
        account.settled += usage.actual_amount
        reservation.amount_settled = usage.actual_amount
        reservation.budget_deviation = usage.actual_amount - reservation.amount_reserved
        reservation.status = (
            ReservationStatus.PARTIALLY_SETTLED
            if usage.actual_amount < reservation.amount_reserved
            else ReservationStatus.SETTLED
        )
        session.add(
            UsageRecord(
                id=str(uuid4()),
                usage_key=usage.usage_key,
                reservation_id=reservation.id,
                operation_id=reservation.operation_id,
                attempt_id=usage.attempt_id,
                provider=usage.provider,
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                image_count=usage.image_count,
                dimensions=usage.dimensions,
                estimated_amount=usage.estimated_amount,
                actual_amount=usage.actual_amount,
                currency=usage.currency,
                pricing_version=usage.pricing_version,
                raw_units=usage.raw_units,
            )
        )
        session.flush()
        return self._reservation_snapshot(reservation)

    def release(self, reservation_id: str) -> ReservationSnapshot:
        with session_scope(self._sessions) as session:
            self.begin_write_transaction(session)
            return self.release_in_transaction(session, reservation_id)

    def release_in_transaction(
        self, session: Session, reservation_id: str
    ) -> ReservationSnapshot:
        reservation = self._require_reservation(session, reservation_id)
        if reservation.status is ReservationStatus.RELEASED:
            return self._reservation_snapshot(reservation)
        if reservation.status not in {
            ReservationStatus.ACTIVE,
            ReservationStatus.HELD_UNKNOWN,
        }:
            raise ReservationConflict(
                f"cannot release reservation in {reservation.status.value}"
            )
        account = self._require_account(session, reservation.account_id)
        if reservation.status is ReservationStatus.ACTIVE:
            account.active_reserved -= reservation.amount_reserved
        else:
            account.held_unknown -= reservation.amount_reserved
        reservation.status = ReservationStatus.RELEASED
        return self._reservation_snapshot(reservation)

    def hold_unknown(self, reservation_id: str) -> ReservationSnapshot:
        with session_scope(self._sessions) as session:
            self.begin_write_transaction(session)
            return self.hold_unknown_in_transaction(session, reservation_id)

    def hold_unknown_in_transaction(
        self, session: Session, reservation_id: str
    ) -> ReservationSnapshot:
        reservation = self._require_reservation(session, reservation_id)
        if reservation.status is ReservationStatus.HELD_UNKNOWN:
            return self._reservation_snapshot(reservation)
        if reservation.status is not ReservationStatus.ACTIVE:
            raise ReservationConflict(
                f"cannot hold reservation in {reservation.status.value}"
            )
        account = self._require_account(session, reservation.account_id)
        account.active_reserved -= reservation.amount_reserved
        account.held_unknown += reservation.amount_reserved
        reservation.status = ReservationStatus.HELD_UNKNOWN
        return self._reservation_snapshot(reservation)

    @staticmethod
    def begin_write_transaction(session: Session) -> None:
        session.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def _require_account(session: Session, account_id: str) -> BudgetAccountRecord:
        account = session.get(BudgetAccountRecord, account_id)
        if account is None:
            raise LedgerRecordNotFound(f"budget account not found: {account_id}")
        return account

    @staticmethod
    def _require_reservation(
        session: Session, reservation_id: str
    ) -> CostReservationRecord:
        reservation = session.get(CostReservationRecord, reservation_id)
        if reservation is None:
            raise LedgerRecordNotFound(
                f"cost reservation not found: {reservation_id}"
            )
        return reservation

    @staticmethod
    def _usage_matches(
        record: UsageRecord,
        reservation_id: str,
        usage: UsageInput,
    ) -> bool:
        return (
            record.reservation_id == reservation_id
            and record.attempt_id == usage.attempt_id
            and record.provider == usage.provider
            and record.model == usage.model
            and record.estimated_amount == usage.estimated_amount
            and record.actual_amount == usage.actual_amount
            and record.currency == usage.currency
            and record.pricing_version == usage.pricing_version
            and record.input_tokens == usage.input_tokens
            and record.output_tokens == usage.output_tokens
            and record.image_count == usage.image_count
            and record.dimensions == usage.dimensions
            and record.raw_units == usage.raw_units
        )

    @staticmethod
    def _account_snapshot(record: BudgetAccountRecord) -> BudgetAccountSnapshot:
        return BudgetAccountSnapshot(
            id=record.id,
            project_id=record.project_id,
            stage=record.stage,
            currency=record.currency,
            hard_limit=record.hard_limit,
            soft_limit=record.soft_limit,
            settled=record.settled,
            active_reserved=record.active_reserved,
            held_unknown=record.held_unknown,
        )

    @staticmethod
    def _reservation_snapshot(record: CostReservationRecord) -> ReservationSnapshot:
        return ReservationSnapshot(
            id=record.id,
            account_id=record.account_id,
            reservation_key=record.reservation_key,
            operation_id=record.operation_id,
            amount_reserved=record.amount_reserved,
            amount_settled=record.amount_settled,
            budget_deviation=record.budget_deviation,
            currency=record.currency,
            pricing_version=record.pricing_version,
            status=record.status,
        )


def _validate_currency(currency: str) -> None:
    if len(currency) != 3 or currency.upper() != currency or not currency.isalpha():
        raise ValueError("currency must be a three-letter uppercase code")
