import asyncio
from dataclasses import replace
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.domain.contracts import PresentationBrief
from ppt_agent.domain.costs import ReservationStatus
from ppt_agent.domain.state_machine import AttemptStatus
from ppt_agent.ledger.costs import CostLedger
from ppt_agent.operations.provider import (
    PrepareAttempt,
    ProviderOperationService,
    RetryRiskConfirmationRequired,
    safe_provider_failure_summary,
)
from ppt_agent.operations.text_runner import TextOperationRunner, TextPricing
from ppt_agent.providers.text import (
    ProviderCallError,
    StructuredTextRequest,
    StructuredTextResult,
    TextUsage,
)
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from ppt_agent.storage.models import Base, ProjectRecord


class FakeProvider:
    def __init__(self, outcome: StructuredTextResult | ProviderCallError) -> None:
        self.outcome = outcome
        self.calls = 0

    async def complete(self, request: StructuredTextRequest) -> StructuredTextResult:
        self.calls += 1
        if isinstance(self.outcome, ProviderCallError):
            raise self.outcome
        return self.outcome


def setup_services():
    directory = Path(".cache/test-text-runner")
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(DatabaseConfig(path=directory / f"{uuid4()}.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Text runner"))
    ledger = CostLedger(sessions)
    account = ledger.create_account(project_id, "planning", "CNY", 10_000)
    service = ProviderOperationService(sessions)
    return engine, ledger, service, account.id


def request() -> StructuredTextRequest:
    return StructuredTextRequest(
        model="deepseek-chat",
        schema_name="presentation_brief",
        schema_version=1,
        json_schema=PresentationBrief.model_json_schema(),
        system_prompt="Return a brief.",
        user_content="Roadmap",
        max_output_tokens=100,
        user_id="local-user",
    )


def operation(account_id: str) -> PrepareAttempt:
    return PrepareAttempt(
        operation_id="brief-operation",
        operation_kind="text.structured",
        request_fingerprint="sha256:caller-placeholder",
        provider="deepseek",
        model="deepseek-chat",
        account_id=account_id,
        reservation_amount=500,
        currency="CNY",
        pricing_version="deepseek-v1",
        idempotency_key="brief-generation-1",
    )


PRICING = TextPricing(
    version="deepseek-v1",
    currency="CNY",
    input_per_million=1_000_000,
    output_per_million=2_000_000,
)


def test_provider_failure_summary_allowlists_normalized_metadata() -> None:
    assert safe_provider_failure_summary(
        {
            "http_status": 429,
            "provider_error_type": "rate_limited",
            "exception_type": "ReadError",
        }
    ) == {
        "http_status": 429,
        "provider_error_type": "rate_limited",
        "exception_type": "ReadError",
    }
    assert safe_provider_failure_summary(
        {
            "http_status": 400,
            "provider_error_type": "api_key=secret",
            "exception_type": "CredentialException",
        }
    ) == {"http_status": 400}
    assert safe_provider_failure_summary(
        {"provider_error_type": ["rate_limited"], "exception_type": {"secret": 1}}
    ) == {}


def success(value: dict[str, object] | None = None) -> StructuredTextResult:
    return StructuredTextResult(
        response_id="resp-1",
        value=value or {"topic": "Roadmap"},
        usage=TextUsage(input_tokens=10, output_tokens=5),
        response_summary={"response_id": "resp-1", "status": "completed"},
    )


def test_success_settles_usage_and_selects_attempt() -> None:
    engine, ledger, service, account_id = setup_services()
    provider = FakeProvider(success())
    try:
        result = asyncio.run(
            TextOperationRunner(service, provider).execute(
                request=request(),
                output_type=PresentationBrief,
                operation=operation(account_id),
                pricing=PRICING,
            )
        )
        attempt = service.get_attempt(result.attempt_id)
        aggregate = service.get_operation(result.operation_id)
        reservation = ledger.get_reservation(attempt.reservation_id)
    finally:
        engine.dispose()

    assert result.output.topic == "Roadmap"
    assert attempt.status is AttemptStatus.SUCCEEDED
    assert aggregate.selected_attempt_id == attempt.id
    assert reservation.amount_settled == 20


def test_schema_failure_with_usage_is_charged_and_attempt_fails() -> None:
    engine, ledger, service, account_id = setup_services()
    provider = FakeProvider(success({"topic": "Roadmap", "page_count": 99}))
    try:
        with pytest.raises(ProviderCallError, match="content_rejected"):
            asyncio.run(
                TextOperationRunner(service, provider).execute(
                    request=request(),
                    output_type=PresentationBrief,
                    operation=operation(account_id),
                    pricing=PRICING,
                )
            )
        attempt = service.list_attempts("brief-operation")[0]
        reservation = ledger.get_reservation(attempt.reservation_id)
    finally:
        engine.dispose()

    assert attempt.status is AttemptStatus.FAILED
    assert attempt.usage_record_id is not None
    assert reservation.amount_settled == 20


def test_confirmed_uncharged_failure_releases_reservation(caplog) -> None:
    engine, ledger, service, account_id = setup_services()
    provider = FakeProvider(
        ProviderCallError(
            "invalid_request",
            False,
            True,
            True,
            None,
            {
                "http_status": 400,
                "provider_error_type": "invalid_request",
                "raw_response": {"api_key": "must-not-persist"},
            },
        )
    )
    caplog.set_level(logging.INFO, logger="ppt_agent.operations.text_runner")
    try:
        with pytest.raises(ProviderCallError, match="invalid_request"):
            asyncio.run(
                TextOperationRunner(service, provider).execute(
                    request=request(),
                    output_type=PresentationBrief,
                    operation=operation(account_id),
                    pricing=PRICING,
                )
            )
        attempt = service.list_attempts("brief-operation")[0]
        reservation = ledger.get_reservation(attempt.reservation_id)
    finally:
        engine.dispose()

    assert attempt.status is AttemptStatus.FAILED
    assert attempt.response_summary == {
        "http_status": 400,
        "provider_error_type": "invalid_request",
    }
    assert reservation.status is ReservationStatus.RELEASED
    records = [
        record for record in caplog.records
        if record.name == "ppt_agent.operations.text_runner"
    ]
    assert [record.getMessage() for record in records] == [
        "provider.submitted",
        "provider.failed",
    ]
    assert records[-1].error_code == "invalid_request"
    assert records[-1].http_status == 400


def test_unknown_outcome_holds_budget_and_blocks_automatic_retry(caplog) -> None:
    engine, ledger, service, account_id = setup_services()
    provider = FakeProvider(
        ProviderCallError(
            "unknown_remote_outcome", False, False, False, None, {"phase": "read"}
        )
    )
    runner = TextOperationRunner(service, provider)
    caplog.set_level(logging.INFO, logger="ppt_agent.operations.text_runner")
    try:
        with pytest.raises(ProviderCallError, match="unknown_remote_outcome"):
            asyncio.run(
                runner.execute(
                    request=request(),
                    output_type=PresentationBrief,
                    operation=operation(account_id),
                    pricing=PRICING,
                )
            )
        attempt = service.list_attempts("brief-operation")[0]
        reservation = ledger.get_reservation(attempt.reservation_id)
        with pytest.raises(RetryRiskConfirmationRequired):
            asyncio.run(
                runner.execute(
                    request=request(),
                    output_type=PresentationBrief,
                    operation=replace(operation(account_id)),
                    pricing=PRICING,
                )
            )
    finally:
        engine.dispose()

    assert provider.calls == 1
    assert attempt.status is AttemptStatus.OUTCOME_UNKNOWN
    assert reservation.status is ReservationStatus.HELD_UNKNOWN
    assert any(
        record.getMessage() == "provider.outcome_unknown"
        and record.error_code == "unknown_remote_outcome"
        for record in caplog.records
    )


def test_success_payload_is_reused_after_crash_before_artifact_materialization() -> None:
    engine, _, service, account_id = setup_services()
    first_provider = FakeProvider(success())
    unavailable = FakeProvider(
        ProviderCallError(
            "unknown_remote_outcome", False, False, False, None, {"phase": "must_not_call"}
        )
    )
    try:
        first = asyncio.run(
            TextOperationRunner(service, first_provider).execute(
                request=request(),
                output_type=PresentationBrief,
                operation=operation(account_id),
                pricing=PRICING,
            )
        )
        recovered = asyncio.run(
            TextOperationRunner(service, unavailable).execute(
                request=request(),
                output_type=PresentationBrief,
                operation=operation(account_id),
                pricing=PRICING,
            )
        )
    finally:
        engine.dispose()

    assert recovered.output == first.output
    assert recovered.usage == first.usage
    assert unavailable.calls == 0


def test_runner_rejects_reservation_below_conservative_maximum() -> None:
    engine, _, service, account_id = setup_services()
    provider = FakeProvider(success())
    try:
        with pytest.raises(ValueError, match="reservation"):
            asyncio.run(
                TextOperationRunner(service, provider).execute(
                    request=request(),
                    output_type=PresentationBrief,
                    operation=replace(operation(account_id), reservation_amount=1),
                    pricing=PRICING,
                )
            )
    finally:
        engine.dispose()

    assert provider.calls == 0


def test_runner_accepts_an_injected_provider_name() -> None:
    engine, _, service, account_id = setup_services()
    provider = FakeProvider(success())
    ark_operation = replace(operation(account_id), provider="volcengine_ark")
    try:
        result = asyncio.run(
            TextOperationRunner(
                service, provider, provider_name="volcengine_ark"
            ).execute(
                request=request(),
                output_type=PresentationBrief,
                operation=ark_operation,
                pricing=PRICING,
            )
        )
    finally:
        engine.dispose()

    assert result.output.topic == "Roadmap"
    assert provider.calls == 1
