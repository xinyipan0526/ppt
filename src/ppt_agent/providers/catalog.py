"""Versioned, trusted catalog of model identities supported by adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ppt_agent.providers.capabilities import (
    ModelCapability,
    ModelCategory,
    ModelLifecycle,
    UnsupportedProviderCapability,
)


ARK_ADAPTER_ID = "volcengine_ark"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
PRICING_VERSION = "official-cn-2026-10-03"
PRICING_SOURCE = "https://docs.volcengine.com/docs/ark/model-pricing?lang=zh"
SEEDREAM_RESULT_HOSTS = (
    "ark-content-generation-cn-beijing.tos-cn-beijing.volces.com",
)


@dataclass(frozen=True, slots=True)
class TokenPricing:
    currency: str
    version: str
    source_url: str
    input_per_million: int
    output_per_million: int
    cached_input_per_million: int = 0

    def __post_init__(self) -> None:
        if self.currency != "CNY" or not self.version or not self.source_url:
            raise ValueError("token pricing identity is incomplete")
        if min(
            self.input_per_million,
            self.output_per_million,
            self.cached_input_per_million,
        ) < 0:
            raise ValueError("token pricing cannot be negative")


@dataclass(frozen=True, slots=True)
class ImagePricingSchedule:
    currency: str
    version: str
    source_url: str
    output_up_to_2_61mp: int
    output_above_2_61mp: int
    additional_reference_image: int
    pixel_threshold: int = 2_610_000

    def __post_init__(self) -> None:
        if self.currency != "CNY" or not self.version or not self.source_url:
            raise ValueError("image pricing identity is incomplete")
        if min(
            self.output_up_to_2_61mp,
            self.output_above_2_61mp,
            self.additional_reference_image,
        ) < 0 or self.pixel_threshold <= 0:
            raise ValueError("image pricing values are invalid")


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    catalog_id: str
    adapter_id: str
    model_id: str
    display_name: str
    category: ModelCategory
    lifecycle: ModelLifecycle
    capabilities: frozenset[ModelCapability]
    recommended: bool
    base_url: str
    result_hosts: tuple[str, ...] = ()
    token_pricing: TokenPricing | None = None
    image_pricing: ImagePricingSchedule | None = None

    def __post_init__(self) -> None:
        if not all((self.catalog_id, self.adapter_id, self.model_id, self.display_name)):
            raise ValueError("model catalog identity is incomplete")
        if self.category is ModelCategory.MULTIMODAL and self.token_pricing is None:
            raise ValueError("multimodal models require token pricing")
        if self.category is ModelCategory.IMAGE and self.image_pricing is None:
            raise ValueError("image models require image pricing")


class ModelCatalog:
    def __init__(self, entries: tuple[ModelCatalogEntry, ...]) -> None:
        by_id = {entry.model_id: entry for entry in entries}
        if len(by_id) != len(entries):
            raise ValueError("duplicate model id")
        self._entries = entries
        self._by_id = by_id

    def require(
        self,
        model_id: str,
        category: ModelCategory,
        *,
        allow_experimental: bool = False,
    ) -> ModelCatalogEntry:
        try:
            entry = self._by_id[model_id]
        except KeyError:
            raise KeyError(f"unsupported model id: {model_id}") from None
        if entry.category is not category:
            raise UnsupportedProviderCapability(
                f"model category is {entry.category.value}, not {category.value}"
            )
        if (
            entry.lifecycle is ModelLifecycle.EXPERIMENTAL
            and not allow_experimental
        ):
            raise UnsupportedProviderCapability("experimental model is not enabled")
        if entry.lifecycle is ModelLifecycle.DEPRECATED:
            raise UnsupportedProviderCapability("deprecated model cannot be selected")
        return entry

    def public_snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "adapter_id": entry.adapter_id,
                "model_id": entry.model_id,
                "display_name": entry.display_name,
                "category": entry.category.value,
                "lifecycle": entry.lifecycle.value,
                "capabilities": [item.value for item in sorted(entry.capabilities)],
                "recommended": entry.recommended,
                "pricing_currency": (
                    entry.token_pricing.currency
                    if entry.token_pricing is not None
                    else entry.image_pricing.currency
                ),
                "pricing_version": (
                    entry.token_pricing.version
                    if entry.token_pricing is not None
                    else entry.image_pricing.version
                ),
            }
            for entry in self._entries
        )


def _token(input_price: int, output_price: int) -> TokenPricing:
    return TokenPricing(
        currency="CNY",
        version=PRICING_VERSION,
        source_url=PRICING_SOURCE,
        input_per_million=input_price,
        output_per_million=output_price,
    )


_MULTIMODAL_CAPABILITIES = frozenset(
    {
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.IMAGE_UNDERSTANDING,
        ModelCapability.OCR,
        ModelCapability.REGION_LOCATION,
    }
)

DEFAULT_MODEL_CATALOG = ModelCatalog(
    (
        ModelCatalogEntry(
            catalog_id="ark.doubao-seed-2-1-pro.260915",
            adapter_id=ARK_ADAPTER_ID,
            model_id="doubao-seed-2-1-pro-260915",
            display_name="Doubao Seed 2.1 Pro",
            category=ModelCategory.MULTIMODAL,
            lifecycle=ModelLifecycle.STABLE,
            capabilities=_MULTIMODAL_CAPABILITIES,
            recommended=True,
            base_url=ARK_BASE_URL,
            token_pricing=_token(600, 3000),
        ),
        ModelCatalogEntry(
            catalog_id="ark.doubao-seed-2-1-lite.260915",
            adapter_id=ARK_ADAPTER_ID,
            model_id="doubao-seed-2-1-lite-260915",
            display_name="Doubao Seed 2.1 Lite",
            category=ModelCategory.MULTIMODAL,
            lifecycle=ModelLifecycle.STABLE,
            capabilities=_MULTIMODAL_CAPABILITIES,
            recommended=False,
            base_url=ARK_BASE_URL,
            token_pricing=_token(80, 270),
        ),
        ModelCatalogEntry(
            catalog_id="ark.deepseek-v4-1-flash.260910",
            adapter_id=ARK_ADAPTER_ID,
            model_id="deepseek-v4-1-flash-260910",
            display_name="DeepSeek V4.1 Flash（实验性）",
            category=ModelCategory.MULTIMODAL,
            lifecycle=ModelLifecycle.EXPERIMENTAL,
            capabilities=_MULTIMODAL_CAPABILITIES,
            recommended=False,
            base_url=ARK_BASE_URL,
            token_pricing=_token(150, 450),
        ),
        ModelCatalogEntry(
            catalog_id="ark.doubao-seedream-5-0-pro.260628",
            adapter_id=ARK_ADAPTER_ID,
            model_id="doubao-seedream-5-0-pro-260628",
            display_name="Doubao Seedream 5.0 Pro",
            category=ModelCategory.IMAGE,
            lifecycle=ModelLifecycle.STABLE,
            capabilities=frozenset(
                {ModelCapability.IMAGE_GENERATION, ModelCapability.IMAGE_EDITING}
            ),
            recommended=True,
            base_url=ARK_BASE_URL,
            result_hosts=SEEDREAM_RESULT_HOSTS,
            image_pricing=ImagePricingSchedule(
                currency="CNY",
                version=PRICING_VERSION,
                source_url=PRICING_SOURCE,
                output_up_to_2_61mp=30,
                output_above_2_61mp=60,
                additional_reference_image=2,
            ),
        ),
    )
)


__all__ = [
    "ARK_ADAPTER_ID",
    "ARK_BASE_URL",
    "DEFAULT_MODEL_CATALOG",
    "ImagePricingSchedule",
    "ModelCatalog",
    "ModelCatalogEntry",
    "TokenPricing",
]
