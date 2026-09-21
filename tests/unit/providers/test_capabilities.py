import pytest
from pydantic import ValidationError

from ppt_agent.providers.capabilities import (
    BillingCapabilities,
    ImageCapabilities,
    OperationMode,
    ProviderCapabilities,
    ProviderFeatureSupport,
    UnsupportedProviderCapability,
)


def seedream_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        provider="volcengine",
        model="seedream-configured-model",
        operation_mode=OperationMode.ASYNC,
        supports=ProviderFeatureSupport(
            idempotency_key=False,
            result_query=True,
            cancellation=False,
            structured_output=False,
        ),
        image=ImageCapabilities(
            input_formats=("jpeg", "png"),
            max_reference_images=10,
            max_input_bytes=15_728_640,
            max_pixels=16_777_216,
            min_aspect_ratio=0.333,
            max_aspect_ratio=3.0,
            native_mask=False,
        ),
        billing=BillingCapabilities(unit="generated_image", region="cn"),
        data_policy_ref="volcengine-cn-current",
    )


def test_image_request_is_checked_against_provider_limits() -> None:
    capabilities = seedream_capabilities()

    capabilities.validate_image_request(
        reference_images=10,
        largest_input_bytes=15_000_000,
        largest_input_pixels=16_000_000,
        aspect_ratio=16 / 9,
        require_native_mask=False,
    )

    with pytest.raises(UnsupportedProviderCapability, match="reference images"):
        capabilities.validate_image_request(
            reference_images=11,
            largest_input_bytes=1_000,
            largest_input_pixels=1_000,
            aspect_ratio=1.0,
            require_native_mask=False,
        )


def test_native_mask_requirement_cannot_silently_degrade() -> None:
    with pytest.raises(UnsupportedProviderCapability, match="native mask"):
        seedream_capabilities().validate_image_request(
            reference_images=1,
            largest_input_bytes=1_000,
            largest_input_pixels=1_000,
            aspect_ratio=1.0,
            require_native_mask=True,
        )


def test_capability_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProviderCapabilities.model_validate(
            {
                **seedream_capabilities().model_dump(),
                "undocumented_feature": True,
            }
        )

