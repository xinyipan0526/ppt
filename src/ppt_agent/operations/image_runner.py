"""Durable orchestration for one image Provider submission."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING

from ppt_agent.ledger.costs import UsageInput
from ppt_agent.operations.provider import (
    PrepareAttempt,
    ProviderOperationService,
    safe_provider_failure_summary,
)
from ppt_agent.providers.image import (
    ImageGenerationProvider,
    ImageGenerationRequest,
    ImageProviderCallError,
    ImageUsage,
    sanitize_provider_diagnostic_identifier,
    sanitize_provider_error_message,
)
from ppt_agent.providers.result_download import ResultDownloader

if TYPE_CHECKING:
    from ppt_agent.providers.catalog import ImagePricingSchedule


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImagePricing:
    version: str
    currency: str
    output_up_to_threshold: int
    output_above_threshold: int | None = None
    additional_reference_image: int = 0
    pixel_threshold: int = 2_610_000

    def __post_init__(self) -> None:
        if not self.version or not self.currency:
            raise ValueError("image pricing identity is required")
        if self.output_above_threshold is None:
            object.__setattr__(
                self, "output_above_threshold", self.output_up_to_threshold
            )
        if min(
            self.output_up_to_threshold,
            self.output_above_threshold,
            self.additional_reference_image,
        ) < 0 or self.pixel_threshold <= 0:
            raise ValueError("image pricing values are invalid")

    @classmethod
    def from_schedule(cls, schedule: ImagePricingSchedule) -> ImagePricing:
        return cls(
            version=schedule.version,
            currency=schedule.currency,
            output_up_to_threshold=schedule.output_up_to_2_61mp,
            output_above_threshold=schedule.output_above_2_61mp,
            additional_reference_image=schedule.additional_reference_image,
            pixel_threshold=schedule.pixel_threshold,
        )

    @property
    def per_image_minor_units(self) -> int:
        """Compatibility maximum for callers that have not yet supplied a request."""
        assert self.output_above_threshold is not None
        return max(self.output_up_to_threshold, self.output_above_threshold)

    def quote(
        self,
        *,
        width: int,
        height: int,
        generated_images: int,
        input_images: int,
    ) -> int:
        if width <= 0 or height <= 0 or generated_images <= 0 or input_images < 0:
            raise ValueError("positive dimensions and generated image count are required")
        assert self.output_above_threshold is not None
        output_price = (
            self.output_up_to_threshold
            if width * height <= self.pixel_threshold
            else self.output_above_threshold
        )
        reference_price = (
            max(input_images - 1, 0) * self.additional_reference_image
        )
        return generated_images * output_price + reference_price

    def reserve(self, request: ImageGenerationRequest) -> int:
        return self.quote(
            width=request.width,
            height=request.height,
            generated_images=1,
            input_images=len(request.reference_images),
        )


@dataclass(frozen=True, slots=True)
class ImageOperationResult:
    attempt_id: str
    operation_id: str
    content: bytes
    media_type: str
    width: int
    height: int
    usage: ImageUsage


class ImageOperationRunner:
    def __init__(self, operations: ProviderOperationService, provider: ImageGenerationProvider, provider_name: str, *, downloader: ResultDownloader | None = None) -> None:
        self._operations = operations
        self._provider = provider
        self._provider_name = provider_name
        self._downloader = downloader

    async def execute(self, *, request: ImageGenerationRequest, operation: PrepareAttempt, pricing: ImagePricing) -> ImageOperationResult:
        if operation.provider != self._provider_name or operation.model != request.model:
            raise ValueError("operation provider/model must match image request")
        if operation.currency != pricing.currency or operation.pricing_version != pricing.version:
            raise ValueError("operation pricing must match image pricing")
        if operation.reservation_amount < pricing.reserve(request):
            raise ValueError("reservation must cover the requested image generation")
        fingerprint = request.fingerprint()
        stored = self._operations.get_binary_result(operation.operation_id)
        if stored is not None:
            if stored.request_fingerprint != fingerprint or stored.result_kind not in {"base64", "url"}:
                raise ValueError("stored image result identity does not match request")
            if stored.result_kind == "url":
                if self._downloader is None:
                    raise RuntimeError("image result downloader is required for URL results")
                content, media_type, width, height = await self._downloader.download(stored.result_value)
                stored = self._operations.complete_binary_download(
                    operation.operation_id, attempt_id=stored.attempt_id,
                    encoded_content=base64.b64encode(content).decode("ascii"),
                    media_type=media_type, dimensions=f"{width}x{height}",
                )
            content = base64.b64decode(stored.result_value, validate=True)
            width, height = (int(value) for value in stored.usage["dimensions"].split("x"))
            usage = ImageUsage(
                stored.usage["generated_images"], stored.usage["output_tokens"], stored.usage["input_images"]
            )
            return ImageOperationResult(stored.attempt_id, stored.operation_id, content, stored.media_type, width, height, usage)
        prepared = self._operations.prepare_attempt(replace(operation, request_fingerprint=fingerprint))
        self._operations.commit_submission_intent(prepared.id)
        logger.info(
            "provider.submitted",
            extra=self._log_context(
                operation, status="submitted", event_type="provider.submitted"
            ),
        )
        try:
            result = await self._provider.generate(request)
        except ImageProviderCallError as exc:
            summary = safe_provider_failure_summary(exc.safe_summary)
            log_summary: dict[str, int | str] = dict(summary)
            provider_message = sanitize_provider_error_message(
                exc.safe_summary.get("provider_error_message"),
                sensitive_values=(request.prompt, request.edit_instruction),
            )
            if provider_message is not None:
                log_summary["provider_error_message"] = provider_message
            for key in ("provider_error_code", "provider_request_id"):
                identifier = sanitize_provider_diagnostic_identifier(
                    exc.safe_summary.get(key)
                )
                if identifier is not None:
                    log_summary[key] = identifier
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(
                    prepared.id,
                    self._usage(
                        prepared.id,
                        operation,
                        pricing,
                        exc.usage,
                        request.width,
                        request.height,
                    ),
                    exc.code,
                    summary,
                )
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id, error_code=exc.code, confirmed_uncharged=True,
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
                        **log_summary,
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
                    **log_summary,
                ),
            )
            raise
        content = result.content
        media_type, width, height = result.media_type, result.width, result.height
        if result.result_url is not None:
            if self._downloader is None:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(prepared.id)
                raise RuntimeError("image result downloader is required for URL results")
            image_usage = {
                "generated_images": result.usage.generated_images,
                "output_tokens": result.usage.output_tokens,
                "input_images": result.usage.input_images,
                "dimensions": f"{width}x{height}",
            }
            self._operations.record_success(
                prepared.id,
                self._usage(prepared.id, operation, pricing, result.usage, width, height),
                result.response_summary,
                binary_result={
                    "result_kind": "url", "result_value": result.result_url,
                    "media_type": media_type, "usage": image_usage,
                    "download_status": "pending",
                },
            )
            content, media_type, width, height = await self._downloader.download(result.result_url)
            self._operations.complete_binary_download(
                operation.operation_id, attempt_id=prepared.id,
                encoded_content=base64.b64encode(content).decode("ascii"),
                media_type=media_type, dimensions=f"{width}x{height}",
            )
            return ImageOperationResult(prepared.id, prepared.operation_id, content, media_type, width, height, result.usage)
        if content is None:
            raise RuntimeError("image Provider returned no durable content")
        image_usage = {
            "generated_images": result.usage.generated_images,
            "output_tokens": result.usage.output_tokens,
            "input_images": result.usage.input_images,
            "dimensions": f"{width}x{height}",
        }
        self._operations.record_success(
            prepared.id,
            self._usage(prepared.id, operation, pricing, result.usage, width, height),
            result.response_summary,
            binary_result={
                "result_kind": "base64",
                "result_value": base64.b64encode(content).decode("ascii"),
                "media_type": media_type,
                "usage": image_usage,
                "download_status": "stored",
            },
        )
        return ImageOperationResult(prepared.id, prepared.operation_id, content, media_type, width, height, result.usage)

    @staticmethod
    def _log_context(
        operation: PrepareAttempt, **context: int | str | None
    ) -> dict[str, int | str]:
        values: dict[str, int | str | None] = {
            "run_id": operation.run_id,
            "step_id": operation.step_id,
            "page_id": operation.page_id,
            "operation_id": operation.operation_id,
            "provider": operation.provider,
            "model": operation.model,
            **context,
        }
        return {key: value for key, value in values.items() if value is not None}

    def _usage(self, attempt_id: str, operation: PrepareAttempt, pricing: ImagePricing, usage: ImageUsage, width: int, height: int) -> UsageInput:
        amount = pricing.quote(
            width=width,
            height=height,
            generated_images=usage.generated_images,
            input_images=usage.input_images,
        )
        return UsageInput(
            usage_key=f"image-usage:{attempt_id}", attempt_id=attempt_id,
            provider=operation.provider, model=operation.model,
            estimated_amount=operation.reservation_amount, actual_amount=amount,
            currency=pricing.currency, pricing_version=pricing.version,
            output_tokens=usage.output_tokens, image_count=usage.generated_images,
            dimensions=f"{width}x{height}",
            raw_units={"input_images": usage.input_images},
        )
