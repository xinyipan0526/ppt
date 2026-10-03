"""Capability-oriented provider bundle construction."""

from __future__ import annotations

from dataclasses import dataclass

from ppt_agent.config.settings import Settings
from ppt_agent.operations.image_runner import ImagePricing
from ppt_agent.operations.text_runner import TextPricing
from ppt_agent.operations.vision_runner import VisionPricing
from ppt_agent.providers.ark import ArkMultimodalAdapter
from ppt_agent.providers.capabilities import ModelCategory
from ppt_agent.providers.catalog import (
    ARK_ADAPTER_ID,
    DEFAULT_MODEL_CATALOG,
    ModelCatalog,
)
from ppt_agent.providers.deterministic import (
    DeterministicImageProvider,
    DeterministicOCRProvider,
    DeterministicTextProvider,
    DeterministicVisionProvider,
)
from ppt_agent.providers.image import ImageGenerationProvider
from ppt_agent.providers.result_download import ResultDownloader
from ppt_agent.providers.seedream import SeedreamProvider
from ppt_agent.providers.text import StructuredTextProvider
from ppt_agent.providers.vision import OCRProvider, VisionUnderstandingProvider
from ppt_agent.runtime.provider_config import ModelRuntimeSnapshot


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    mode: str
    provider_name: str
    structured_text: StructuredTextProvider
    vision: VisionUnderstandingProvider
    ocr: OCRProvider
    image: ImageGenerationProvider
    multimodal_model_id: str
    image_model_id: str
    text_pricing: TextPricing
    vision_pricing: VisionPricing
    image_pricing: ImagePricing
    result_downloader: ResultDownloader | None
    configuration_version: int


class AdapterRegistry:
    def __init__(
        self,
        api_key: str | None,
        *,
        catalog: ModelCatalog = DEFAULT_MODEL_CATALOG,
    ) -> None:
        self._api_key = api_key
        self._catalog = catalog

    def build(self, snapshot: ModelRuntimeSnapshot) -> ProviderBundle:
        if snapshot.mode != "cloud":
            return self._deterministic(snapshot.configuration_version)
        if snapshot.adapter_id != ARK_ADAPTER_ID or not self._api_key:
            raise ValueError("qualified Ark adapter and credential are required")
        if snapshot.multimodal_model is None or snapshot.image_model is None:
            raise ValueError("both model categories are required")
        return self._cloud(
            multimodal_model_id=snapshot.multimodal_model.model_id,
            image_model_id=snapshot.image_model.model_id,
            pricing_overrides=snapshot.pricing_overrides,
            configuration_version=snapshot.configuration_version,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        configuration_version: int = 1,
        catalog: ModelCatalog = DEFAULT_MODEL_CATALOG,
    ) -> ProviderBundle:
        registry = cls(settings.ark_api_key, catalog=catalog)
        if settings.provider_mode != "cloud":
            return registry._deterministic(configuration_version)
        if settings.adapter_id != ARK_ADAPTER_ID:
            raise ValueError("unsupported adapter")
        if not settings.multimodal_model_id or not settings.image_model_id:
            raise ValueError("both model categories are required")
        return registry._cloud(
            multimodal_model_id=settings.multimodal_model_id,
            image_model_id=settings.image_model_id,
            pricing_overrides=settings.pricing_overrides,
            configuration_version=configuration_version,
        )

    def _cloud(
        self,
        *,
        multimodal_model_id: str,
        image_model_id: str,
        pricing_overrides: dict[str, object],
        configuration_version: int,
    ) -> ProviderBundle:
        if not self._api_key:
            raise ValueError("Ark credential is required")
        multimodal = self._catalog.require(
            multimodal_model_id, ModelCategory.MULTIMODAL
        )
        image = self._catalog.require(image_model_id, ModelCategory.IMAGE)
        if multimodal.token_pricing is None or image.image_pricing is None:
            raise ValueError("catalog pricing is incomplete")
        token = multimodal.token_pricing
        input_price = self._override(
            pricing_overrides, "multimodal_input_per_million", token.input_per_million
        )
        output_price = self._override(
            pricing_overrides, "multimodal_output_per_million", token.output_per_million
        )
        cached_price = self._override(
            pricing_overrides,
            "multimodal_cached_input_per_million",
            token.cached_input_per_million,
        )
        schedule = image.image_pricing
        image_pricing = ImagePricing(
            version=schedule.version,
            currency=schedule.currency,
            output_up_to_threshold=self._override(
                pricing_overrides,
                "image_output_up_to_2_61mp",
                schedule.output_up_to_2_61mp,
            ),
            output_above_threshold=self._override(
                pricing_overrides,
                "image_output_above_2_61mp",
                schedule.output_above_2_61mp,
            ),
            additional_reference_image=self._override(
                pricing_overrides,
                "image_additional_reference",
                schedule.additional_reference_image,
            ),
            pixel_threshold=schedule.pixel_threshold,
        )
        adapter = ArkMultimodalAdapter(self._api_key, multimodal.base_url)
        return ProviderBundle(
            mode="cloud",
            provider_name=ARK_ADAPTER_ID,
            structured_text=adapter,
            vision=adapter,
            ocr=adapter,
            image=SeedreamProvider(self._api_key, image.base_url),
            multimodal_model_id=multimodal.model_id,
            image_model_id=image.model_id,
            text_pricing=TextPricing(
                token.version,
                token.currency,
                input_price,
                output_price,
                cached_price,
            ),
            vision_pricing=VisionPricing(
                token.version, token.currency, input_price, output_price
            ),
            image_pricing=image_pricing,
            result_downloader=ResultDownloader(allowed_hosts=set(image.result_hosts)),
            configuration_version=configuration_version,
        )

    @staticmethod
    def _deterministic(configuration_version: int) -> ProviderBundle:
        return ProviderBundle(
            mode="deterministic",
            provider_name="deterministic",
            structured_text=DeterministicTextProvider(),
            vision=DeterministicVisionProvider(),
            ocr=DeterministicOCRProvider(),
            image=DeterministicImageProvider(),
            multimodal_model_id="deterministic-v1",
            image_model_id="deterministic-image-v1",
            text_pricing=TextPricing("deterministic-v1", "CNY", 0, 0),
            vision_pricing=VisionPricing("deterministic-v1", "CNY", 0, 0),
            image_pricing=ImagePricing("deterministic-v1", "CNY", 0),
            result_downloader=None,
            configuration_version=configuration_version,
        )

    @staticmethod
    def _override(values: dict[str, object], name: str, default: int) -> int:
        value = values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"invalid pricing override: {name}")
        if int(value) != value:
            raise ValueError(f"pricing override must use minor currency units: {name}")
        return int(value)


__all__ = ["AdapterRegistry", "ProviderBundle"]
