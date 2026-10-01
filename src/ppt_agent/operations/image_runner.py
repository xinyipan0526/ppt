"""Durable orchestration for one image Provider submission."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from ppt_agent.ledger.costs import UsageInput
from ppt_agent.operations.provider import PrepareAttempt, ProviderOperationService
from ppt_agent.providers.image import (
    ImageGenerationProvider,
    ImageGenerationRequest,
    ImageProviderCallError,
    ImageUsage,
)
from ppt_agent.providers.result_download import ResultDownloader


@dataclass(frozen=True, slots=True)
class ImagePricing:
    version: str
    currency: str
    per_image_minor_units: int


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
        if operation.reservation_amount < pricing.per_image_minor_units:
            raise ValueError("reservation must cover one generated image")
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
        try:
            result = await self._provider.generate(request)
        except ImageProviderCallError as exc:
            if exc.usage is not None and exc.billing_known:
                self._operations.record_charged_failure(prepared.id, self._usage(prepared.id, operation, pricing, exc.usage, request.width, request.height), exc.code, exc.safe_summary)
            elif exc.confirmed_uncharged:
                self._operations.record_known_failure(
                    prepared.id, error_code=exc.code, confirmed_uncharged=True,
                    retry_at=(datetime.now(UTC) + timedelta(seconds=30)) if exc.retryable else None,
                )
            else:
                self._operations.begin_recovery(prepared.id)
                self._operations.mark_outcome_unknown(prepared.id)
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

    def _usage(self, attempt_id: str, operation: PrepareAttempt, pricing: ImagePricing, usage: ImageUsage, width: int, height: int) -> UsageInput:
        amount = usage.generated_images * pricing.per_image_minor_units
        return UsageInput(
            usage_key=f"image-usage:{attempt_id}", attempt_id=attempt_id,
            provider=operation.provider, model=operation.model,
            estimated_amount=operation.reservation_amount, actual_amount=amount,
            currency=pricing.currency, pricing_version=pricing.version,
            output_tokens=usage.output_tokens, image_count=usage.generated_images,
            dimensions=f"{width}x{height}",
            raw_units={"input_images": usage.input_images},
        )
