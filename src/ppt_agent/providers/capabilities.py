"""Versioned provider capability declarations and request negotiation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class UnsupportedProviderCapability(ValueError):
    """Raised when a request requires a capability the model lacks."""


class OperationMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderFeatureSupport(CapabilityModel):
    idempotency_key: bool
    result_query: bool
    cancellation: bool
    structured_output: bool
    streaming: bool = False


class ImageCapabilities(CapabilityModel):
    input_formats: tuple[str, ...]
    max_reference_images: int = Field(ge=0)
    max_input_bytes: int = Field(gt=0)
    max_pixels: int = Field(gt=0)
    min_aspect_ratio: float = Field(gt=0)
    max_aspect_ratio: float = Field(gt=0)
    native_mask: bool
    bounding_box_overlay: bool = False
    scribble_overlay: bool = False


class BillingCapabilities(CapabilityModel):
    unit: str = Field(min_length=1)
    region: str = Field(min_length=1)


class VisionCapabilities(CapabilityModel):
    input_formats: tuple[str, ...]
    max_input_bytes: int = Field(gt=0)
    max_pixels: int = Field(gt=0)
    structured_output: bool
    positional_ocr: bool = False


class ProviderCapabilities(CapabilityModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    operation_mode: OperationMode
    supports: ProviderFeatureSupport
    image: ImageCapabilities | None = None
    vision: VisionCapabilities | None = None
    billing: BillingCapabilities
    data_policy_ref: str = Field(min_length=1)
    result_url_ttl_seconds: int | None = Field(default=None, gt=0)

    def validate_image_request(
        self,
        *,
        reference_images: int,
        largest_input_bytes: int,
        largest_input_pixels: int,
        aspect_ratio: float,
        require_native_mask: bool,
    ) -> None:
        image = self.image
        if image is None:
            raise UnsupportedProviderCapability("provider has no image capability")
        if reference_images > image.max_reference_images:
            raise UnsupportedProviderCapability(
                f"reference images exceed limit {image.max_reference_images}"
            )
        if largest_input_bytes > image.max_input_bytes:
            raise UnsupportedProviderCapability("input image exceeds byte limit")
        if largest_input_pixels > image.max_pixels:
            raise UnsupportedProviderCapability("input image exceeds pixel limit")
        if not image.min_aspect_ratio <= aspect_ratio <= image.max_aspect_ratio:
            raise UnsupportedProviderCapability("image aspect ratio is unsupported")
        if require_native_mask and not image.native_mask:
            raise UnsupportedProviderCapability("native mask is unsupported")
