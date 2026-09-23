"""Durable orchestration for one structured text provider submission."""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from collections.abc import Callable

from pydantic import ValidationError

from ppt_agent.domain.contracts import ContractModel
from ppt_agent.ledger.costs import UsageInput
from ppt_agent.operations.provider import PrepareAttempt, ProviderOperationService
from ppt_agent.providers.text import (
    ProviderCallError,
    StructuredTextProvider,
    StructuredTextRequest,
    TextUsage,
)


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
    ) -> None:
        self._operations = operations
        self._provider = provider

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
        prepared = self._operations.prepare_attempt(
            replace(operation, request_fingerprint=self._fingerprint(request))
        )
        self._operations.commit_submission_intent(prepared.id)
        try:
            result = await self._provider.complete(request)
        except ProviderCallError as exc:
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(
                    prepared.id,
                    self._usage_input(prepared.id, operation, pricing, exc.usage),
                    exc.code,
                    exc.safe_summary,
                )
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id,
                    error_code=exc.code,
                    confirmed_uncharged=True,
                )
            else:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(prepared.id)
            raise

        try:
            output = output_type.model_validate(result.value)
            if validate_output is not None:
                validate_output(output)
        except (ValidationError, ValueError) as exc:
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
                },
            )
            self._operations.record_charged_failure(
                prepared.id,
                self._usage_input(prepared.id, operation, pricing, result.usage),
                error.code,
                error.safe_summary,
            )
            raise error from exc

        self._operations.record_success(
            prepared.id,
            self._usage_input(prepared.id, operation, pricing, result.usage),
            result.response_summary,
        )
        return TextOperationResult(
            attempt_id=prepared.id,
            operation_id=prepared.operation_id,
            output=output,
            usage=result.usage,
        )

    @staticmethod
    def _validate_configuration(
        request: StructuredTextRequest,
        operation: PrepareAttempt,
        pricing: TextPricing,
    ) -> None:
        if operation.provider != "deepseek":
            raise ValueError("structured text operation provider must be deepseek")
        if operation.model != request.model:
            raise ValueError("operation model must match request model")
        if operation.currency != pricing.currency:
            raise ValueError("operation currency must match pricing")
        if operation.pricing_version != pricing.version:
            raise ValueError("operation pricing version must match pricing")

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
