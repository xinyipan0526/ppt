from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.ledger.costs import (
    BudgetExceeded,
    CostLedger,
    CurrencyMismatch,
    ReservationConflict,
    ReservationStatus,
    UsageInput,
)
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, ProjectRecord


def create_ledger() -> tuple[CostLedger, object, str]:
    directory = Path(".cache/test-ledger")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(
        DatabaseConfig(path=directory / f"{uuid4()}.db", busy_timeout_ms=10_000)
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Ledger test"))
    return CostLedger(sessions), engine, project_id


def test_concurrent_reservations_do_not_oversell_hard_budget() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(
        project_id=project_id,
        stage="page_generation",
        currency="CNY",
        hard_limit=1_000,
    )

    def try_reserve(index: int) -> bool:
        try:
            ledger.reserve(
                account_id=account.id,
                operation_id=f"operation-{index}",
                amount=300,
                currency="CNY",
                pricing_version="seedream-v1",
            )
            return True
        except BudgetExceeded:
            return False

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(try_reserve, range(8)))
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert outcomes.count(True) == 3
    assert outcomes.count(False) == 5
    assert updated.active_reserved == 900
    assert updated.settled == 0
    assert updated.held_unknown == 0


@pytest.mark.parametrize(
    ("actual_amount", "expected_status", "expected_deviation"),
    [
        (200, ReservationStatus.PARTIALLY_SETTLED, -100),
        (300, ReservationStatus.SETTLED, 0),
        (400, ReservationStatus.SETTLED, 100),
    ],
)
def test_settlement_records_actual_cost_and_deviation(
    actual_amount: int,
    expected_status: ReservationStatus,
    expected_deviation: int,
) -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "planning", "CNY", 1_000)
    reservation = ledger.reserve(
        account.id,
        "operation-1",
        300,
        "CNY",
        "deepseek-v1",
    )

    try:
        settled = ledger.settle(
            reservation.id,
            UsageInput(
                usage_key="usage-1",
                attempt_id="attempt-1",
                provider="deepseek",
                model="deepseek-chat",
                estimated_amount=300,
                actual_amount=actual_amount,
                currency="CNY",
                pricing_version="deepseek-v1",
                input_tokens=1_000,
                output_tokens=500,
            ),
        )
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert settled.status is expected_status
    assert settled.amount_settled == actual_amount
    assert settled.budget_deviation == expected_deviation
    assert updated.active_reserved == 0
    assert updated.settled == actual_amount


def test_repeated_usage_callback_does_not_settle_twice() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "planning", "CNY", 1_000)
    reservation = ledger.reserve(account.id, "operation-1", 300, "CNY", "v1")
    usage = UsageInput(
        usage_key="provider-usage-1",
        attempt_id="attempt-1",
        provider="deepseek",
        model="deepseek-chat",
        estimated_amount=300,
        actual_amount=250,
        currency="CNY",
        pricing_version="v1",
    )

    try:
        first = ledger.settle(reservation.id, usage)
        repeated = ledger.settle(reservation.id, usage)
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert repeated == first
    assert updated.settled == 250
    assert updated.active_reserved == 0


def test_unknown_outcome_keeps_amount_unavailable() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "generation", "CNY", 1_000)
    reservation = ledger.reserve(account.id, "operation-1", 900, "CNY", "v1")

    try:
        held = ledger.hold_unknown(reservation.id)
        with pytest.raises(BudgetExceeded):
            ledger.reserve(account.id, "operation-2", 200, "CNY", "v1")
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert held.status is ReservationStatus.HELD_UNKNOWN
    assert updated.active_reserved == 0
    assert updated.held_unknown == 900


def test_release_returns_reserved_amount_to_available_budget() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "generation", "CNY", 1_000)
    reservation = ledger.reserve(account.id, "operation-1", 900, "CNY", "v1")

    try:
        released = ledger.release(reservation.id)
        replacement = ledger.reserve(account.id, "operation-2", 1_000, "CNY", "v1")
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert released.status is ReservationStatus.RELEASED
    assert replacement.status is ReservationStatus.ACTIVE
    assert updated.active_reserved == 1_000


def test_currency_mismatch_is_rejected_without_changing_account() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "planning", "CNY", 1_000)

    try:
        with pytest.raises(CurrencyMismatch):
            ledger.reserve(account.id, "operation-1", 300, "USD", "v1")
        unchanged = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert unchanged.active_reserved == 0


def test_reserving_same_operation_is_idempotent_but_changed_amount_conflicts() -> None:
    ledger, engine, project_id = create_ledger()
    account = ledger.create_account(project_id, "planning", "CNY", 1_000)

    try:
        first = ledger.reserve(account.id, "operation-1", 300, "CNY", "v1")
        repeated = ledger.reserve(account.id, "operation-1", 300, "CNY", "v1")
        with pytest.raises(ReservationConflict):
            ledger.reserve(account.id, "operation-1", 400, "CNY", "v1")
        updated = ledger.get_account(account.id)
    finally:
        engine.dispose()

    assert repeated == first
    assert updated.active_reserved == 300
