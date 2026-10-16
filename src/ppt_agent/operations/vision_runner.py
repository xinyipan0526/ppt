"""Durable structured vision and OCR operation orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ppt_agent.ledger.costs import UsageInput
from ppt_agent.operations.provider import PrepareAttempt, ProviderOperationService
from ppt_agent.providers.vision import (
    OCRProvider,
    OCRRequest,
    OCRResult,
    VisionProviderCallError,
    VisionRequest,
    VisionUnderstandingProvider,
    VisionUsage,
)


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class VisionPricing:
    version: str
    currency: str
    input_per_million: int
    output_per_million: int


@dataclass(frozen=True, slots=True)
class VisionOperationResult:
    attempt_id: str
    operation_id: str
    output: BaseModel
    usage: VisionUsage


class VisionOperationRunner:
    def __init__(self, operations: ProviderOperationService, provider: VisionUnderstandingProvider, provider_name: str) -> None:
        self._operations = operations
        self._provider = provider
        self._provider_name = provider_name

    async def execute(self, *, request: VisionRequest, output_type: type[OutputT], operation: PrepareAttempt, pricing: VisionPricing) -> VisionOperationResult:
        if operation.provider != self._provider_name or operation.model != request.model:
            raise ValueError("operation provider/model must match vision request")
        if operation.currency != pricing.currency or operation.pricing_version != pricing.version:
            raise ValueError("operation pricing must match vision pricing")
        maximum = conservative_vision_reservation(request.max_output_tokens, len(request.image), pricing)
        if operation.reservation_amount < maximum:
            raise ValueError("reservation must cover conservative vision maximum")
        fingerprint = request.fingerprint()
        stored = self._operations.get_vision_result(operation.operation_id)
        if stored is not None:
            if stored.request_fingerprint != fingerprint or stored.schema_name != request.schema_name or stored.schema_version != request.schema_version:
                raise ValueError("stored vision result identity does not match request")
            output = output_type.model_validate(stored.output_payload)
            usage = VisionUsage(stored.usage["input_tokens"], stored.usage["output_tokens"])
            return VisionOperationResult(stored.attempt_id, stored.operation_id, output, usage)
        prepared = self._operations.prepare_attempt(replace(operation, request_fingerprint=fingerprint))
        self._operations.commit_submission_intent(prepared.id)
        try:
            result = await self._provider.analyze(request)
        except VisionProviderCallError as exc:
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(prepared.id, _usage_input(prepared.id, operation, pricing, exc.usage), exc.code, exc.safe_summary)
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id, error_code=exc.code, confirmed_uncharged=True,
                    retry_at=(datetime.now(UTC) + timedelta(seconds=30)) if exc.retryable else None,
                )
            else:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(
                    prepared.id,
                    error_code=exc.code,
                    response_summary=exc.safe_summary,
                )
            raise
        try:
            output = output_type.model_validate(result.value)
        except ValidationError as exc:
            error = VisionProviderCallError("content_rejected", False, True, False, result.usage, {"reason": "local_schema_validation_failed", "validation_error_count": exc.error_count()})
            self._operations.record_charged_failure(prepared.id, _usage_input(prepared.id, operation, pricing, result.usage), error.code, error.safe_summary)
            raise error from exc
        usage_payload = {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}
        self._operations.record_success(
            prepared.id, _usage_input(prepared.id, operation, pricing, result.usage),
            result.response_summary,
            vision_result={"schema_name": request.schema_name, "schema_version": request.schema_version, "output_payload": output.model_dump(mode="json"), "usage": usage_payload},
        )
        return VisionOperationResult(prepared.id, prepared.operation_id, output, result.usage)

class OCROperationRunner:
    def __init__(self, operations: ProviderOperationService, provider: OCRProvider, provider_name: str) -> None:
        self._operations = operations
        self._provider = provider
        self._provider_name = provider_name

    async def execute(self, *, request: OCRRequest, operation: PrepareAttempt, pricing: VisionPricing) -> OCRResult:
        if operation.provider != self._provider_name or operation.model != request.model:
            raise ValueError("operation provider/model must match OCR request")
        if operation.currency != pricing.currency or operation.pricing_version != pricing.version:
            raise ValueError("operation pricing must match OCR pricing")
        maximum = conservative_vision_reservation(request.max_output_tokens, len(request.image), pricing)
        if operation.reservation_amount < maximum:
            raise ValueError("reservation must cover conservative OCR maximum")
        fingerprint = request.fingerprint()
        stored = self._operations.get_vision_result(operation.operation_id)
        if stored is not None:
            if stored.request_fingerprint != fingerprint or stored.schema_name != "ocr_report" or stored.schema_version != 1:
                raise ValueError("stored OCR result identity does not match request")
            from ppt_agent.domain.vision import OCRReportV1
            report = OCRReportV1.model_validate(stored.output_payload)
            usage = VisionUsage(stored.usage["input_tokens"], stored.usage["output_tokens"])
            return OCRResult(stored.attempt_id, report, usage, {"replayed": True})
        prepared = self._operations.prepare_attempt(replace(operation, request_fingerprint=fingerprint))
        self._operations.commit_submission_intent(prepared.id)
        try:
            result = await self._provider.recognize(request)
        except VisionProviderCallError as exc:
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(prepared.id, _usage_input(prepared.id, operation, pricing, exc.usage), exc.code, exc.safe_summary)
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id, error_code=exc.code, confirmed_uncharged=True,
                    retry_at=(datetime.now(UTC) + timedelta(seconds=30)) if exc.retryable else None,
                )
            else:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(
                    prepared.id,
                    error_code=exc.code,
                    response_summary=exc.safe_summary,
                )
            raise
        usage_input = _usage_input(prepared.id, operation, pricing, result.usage)
        self._operations.record_success(
            prepared.id, usage_input, result.response_summary,
            vision_result={
                "schema_name": "ocr_report", "schema_version": 1,
                "output_payload": result.report.model_dump(mode="json"),
                "usage": {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens},
            },
        )
        return OCRResult(prepared.id, result.report, result.usage, result.response_summary)


def _usage_input(attempt_id: str, operation: PrepareAttempt, pricing: VisionPricing, usage: VisionUsage) -> UsageInput:
    amount = (usage.input_tokens * pricing.input_per_million + usage.output_tokens * pricing.output_per_million + 999_999) // 1_000_000
    return UsageInput(
        usage_key=f"vision-usage:{attempt_id}", attempt_id=attempt_id,
        provider=operation.provider, model=operation.model,
        estimated_amount=operation.reservation_amount, actual_amount=amount,
        currency=pricing.currency, pricing_version=pricing.version,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
    )


def conservative_vision_reservation(
    max_output_tokens: int, image_bytes: int, pricing: VisionPricing
) -> int:
    # Providers tokenize images differently. One token per byte is intentionally
    # conservative and guarantees the SQLite reservation covers reported usage.
    return max(
        1,
        (
            max_output_tokens * pricing.output_per_million
            + image_bytes * pricing.input_per_million
            + 999_999
        )
        // 1_000_000,
    )
