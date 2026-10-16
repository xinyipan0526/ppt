"""Durable orchestration for one structured text provider submission."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from time import monotonic
from collections.abc import Callable

from pydantic import ValidationError

from ppt_agent.domain.contracts import ContractModel
from ppt_agent.ledger.costs import UsageInput
from ppt_agent.operations.provider import (
    PrepareAttempt,
    ProviderOperationService,
    safe_provider_failure_summary,
)
from ppt_agent.providers.text import (
    ProviderCallError,
    StructuredTextProvider,
    StructuredTextRequest,
    TextUsage,
)
from ppt_agent.providers.image import sanitize_provider_diagnostic_identifier


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TextPricing:
    version: str
    currency: str
    input_per_million: int
    output_per_million: int
    cached_input_per_million: int = 0
    safety_factor_millis: int = 1_200

    def __post_init__(self) -> None:
        if not self.version or not self.currency:
            raise ValueError("pricing identity and currency are required")
        if min(
            self.input_per_million,
            self.output_per_million,
            self.cached_input_per_million,
            self.safety_factor_millis,
        ) < 0:
            raise ValueError("pricing values cannot be negative")


@dataclass(frozen=True, slots=True)
class TextOperationResult:
    attempt_id: str
    operation_id: str
    output: ContractModel
    usage: TextUsage


class TextOperationRunner:
    def __init__(
        self,
        operations: ProviderOperationService,
        provider: StructuredTextProvider,
        *,
        provider_name: str = "deepseek",
    ) -> None:
        if not provider_name:
            raise ValueError("provider_name must be non-empty")
        self._operations = operations
        self._provider = provider
        self._provider_name = provider_name

    async def execute(
        self,
        *,
        request: StructuredTextRequest,
        output_type: type[ContractModel],
        operation: PrepareAttempt,
        pricing: TextPricing,
        validate_output: Callable[[ContractModel], None] | None = None,
    ) -> TextOperationResult:
        self._validate_configuration(request, operation, pricing)
        fingerprint = self._fingerprint(request)
        stored = self._operations.get_text_result(operation.operation_id)
        if stored is not None:
            if (
                stored.request_fingerprint != fingerprint
                or stored.schema_name != request.schema_name
                or stored.schema_version != request.schema_version
            ):
                raise ValueError("stored text result identity does not match request")
            output = output_type.model_validate(stored.output_payload)
            if validate_output is not None:
                validate_output(output)
            usage = TextUsage(**stored.usage)
            return TextOperationResult(
                attempt_id=stored.attempt_id,
                operation_id=stored.operation_id,
                output=output,
                usage=usage,
            )
        prepared = self._operations.prepare_attempt(
            replace(operation, request_fingerprint=fingerprint)
        )
        self._operations.commit_submission_intent(prepared.id)
        logger.info(
            "provider.submitted",
            extra=self._log_context(operation, status="submitted", event_type="provider.submitted"),
        )
        started_at = monotonic()
        try:
            result = await self._provider.complete(request)
        except ProviderCallError as exc:
            summary = safe_provider_failure_summary(exc.safe_summary)
            summary.setdefault("elapsed_seconds", max(0.0, monotonic() - started_at))
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(
                    prepared.id,
                    self._usage_input(prepared.id, operation, pricing, exc.usage),
                    exc.code,
                    summary,
                )
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id,
                    error_code=exc.code,
                    confirmed_uncharged=True,
                    response_summary=summary,
                    retry_at=(datetime.now(UTC) + timedelta(seconds=30)) if exc.retryable else None,
                )
            else:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(
                    prepared.id,
                    error_code=exc.code,
                    response_summary=summary,
                )
                logger.warning(
                    "provider.outcome_unknown",
                    extra=self._log_context(
                        operation,
                        status="outcome_unknown",
                        event_type="provider.outcome_unknown",
                        error_code=exc.code,
                        **summary,
                    ),
                )
                raise
            logger.error(
                "provider.failed",
                extra=self._log_context(
                    operation,
                    status="failed",
                    event_type="provider.failed",
                    error_code=exc.code,
                    **summary,
                ),
            )
            raise

        try:
            output = output_type.model_validate(result.value)
            if validate_output is not None:
                validate_output(output)
        except (ValidationError, ValueError) as exc:
            elapsed_seconds = max(0.0, monotonic() - started_at)
            error = ProviderCallError(
                code="content_rejected",
                retryable=False,
                billing_known=True,
                confirmed_uncharged=False,
                usage=result.usage,
                safe_summary={
                    **result.response_summary,
                    "reason": "local_schema_validation_failed",
                    "validation_error_count": (
                        exc.error_count() if isinstance(exc, ValidationError) else 1
                    ),
                    "elapsed_seconds": elapsed_seconds,
                },
            )
            self._operations.record_charged_failure(
                prepared.id,
                self._usage_input(prepared.id, operation, pricing, result.usage),
                error.code,
                error.safe_summary,
            )
            logger.error(
                "provider.failed",
                extra=self._log_context(
                    operation,
                    status="failed",
                    event_type="provider.failed",
                    error_code=error.code,
                    elapsed_seconds=elapsed_seconds,
                ),
            )
            raise error from exc

        self._operations.record_success(
            prepared.id,
            self._usage_input(prepared.id, operation, pricing, result.usage),
            result.response_summary,
            schema_name=request.schema_name,
            schema_version=request.schema_version,
            output_payload=output.model_dump(mode="json"),
            text_usage={
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cached_tokens": result.usage.cached_tokens,
                "reasoning_tokens": result.usage.reasoning_tokens,
            },
        )
        response_id = sanitize_provider_diagnostic_identifier(result.response_id)
        logger.info(
            "provider.succeeded",
            extra=self._log_context(
                operation,
                status="succeeded",
                event_type="provider.succeeded",
                elapsed_seconds=max(0.0, monotonic() - started_at),
                response_id=response_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            ),
        )
        return TextOperationResult(
            attempt_id=prepared.id,
            operation_id=prepared.operation_id,
            output=output,
            usage=result.usage,
        )

    def _validate_configuration(
        self,
        request: StructuredTextRequest,
        operation: PrepareAttempt,
        pricing: TextPricing,
    ) -> None:
        if operation.provider != self._provider_name:
            raise ValueError(
                "structured text operation provider must match the configured provider"
            )
        if operation.model != request.model:
            raise ValueError("operation model must match request model")
        if operation.currency != pricing.currency:
            raise ValueError("operation currency must match pricing")
        if operation.pricing_version != pricing.version:
            raise ValueError("operation pricing version must match pricing")
        maximum = TextOperationRunner.conservative_reservation(request, pricing)
        if operation.reservation_amount < maximum:
            raise ValueError(
                f"reservation must cover conservative maximum of {maximum} minor units"
            )

    @staticmethod
    def _log_context(
        operation: PrepareAttempt, **context: bool | float | int | str | None
    ) -> dict[str, bool | float | int | str]:
        values: dict[str, bool | float | int | str | None] = {
            "run_id": operation.run_id,
            "step_id": operation.step_id,
            "operation_id": operation.operation_id,
            "provider": operation.provider,
            "model": operation.model,
            **context,
        }
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def conservative_reservation(
        request: StructuredTextRequest, pricing: TextPricing
    ) -> int:
        # UTF-8 bytes are a deliberately conservative upper bound on input tokens.
        worst_input_tokens = len(
            (request.system_prompt + request.user_content).encode("utf-8")
        )
        numerator = (
            worst_input_tokens * pricing.input_per_million
            + request.max_output_tokens * pricing.output_per_million
        )
        return max(
            1,
            (numerator * pricing.safety_factor_millis + 999_999_999)
            // 1_000_000_000,
        )

    @staticmethod
    def _fingerprint(request: StructuredTextRequest) -> str:
        metadata = {
            "model": request.model,
            "schema_name": request.schema_name,
            "schema_version": request.schema_version,
            "schema_hash": sha256(
                json.dumps(
                    request.json_schema,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "system_prompt_hash": sha256(request.system_prompt.encode()).hexdigest(),
            "user_content_hash": sha256(request.user_content.encode()).hexdigest(),
            "max_output_tokens": request.max_output_tokens,
            "user_id": request.user_id,
        }
        digest = sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"sha256:{digest}"

    @staticmethod
    def _usage_input(
        attempt_id: str,
        operation: PrepareAttempt,
        pricing: TextPricing,
        usage: TextUsage,
    ) -> UsageInput:
        uncached = max(usage.input_tokens - usage.cached_tokens, 0)
        numerator = (
            uncached * pricing.input_per_million
            + usage.cached_tokens * pricing.cached_input_per_million
            + usage.output_tokens * pricing.output_per_million
        )
        actual_amount = (numerator + 999_999) // 1_000_000
        return UsageInput(
            usage_key=f"text-usage:{attempt_id}",
            attempt_id=attempt_id,
            provider=operation.provider,
            model=operation.model,
            estimated_amount=operation.reservation_amount,
            actual_amount=actual_amount,
            currency=pricing.currency,
            pricing_version=pricing.version,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            raw_units={
                "cached_tokens": usage.cached_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
        )
