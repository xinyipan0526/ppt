from ppt_agent.config.settings import Settings
from ppt_agent.providers.ark import ArkMultimodalAdapter
from ppt_agent.providers.registry import AdapterRegistry
from ppt_agent.providers.seedream import SeedreamProvider


def cloud_settings() -> Settings:
    return Settings(
        provider_mode="cloud",
        ark_api_key="ark-secret-value",
        multimodal_model_id="doubao-seed-2-1-pro-260915",
        image_model_id="doubao-seedream-5-0-pro-260628",
    )


def test_registry_builds_one_ark_multimodal_adapter_for_three_capabilities() -> None:
    bundle = AdapterRegistry.from_settings(cloud_settings(), configuration_version=7)

    assert isinstance(bundle.structured_text, ArkMultimodalAdapter)
    assert bundle.structured_text is bundle.vision
    assert bundle.vision is bundle.ocr
    assert isinstance(bundle.image, SeedreamProvider)
    assert bundle.provider_name == "volcengine_ark"
    assert bundle.multimodal_model_id == "doubao-seed-2-1-pro-260915"
    assert bundle.image_model_id == "doubao-seedream-5-0-pro-260628"
    assert bundle.configuration_version == 7
    assert bundle.text_pricing.input_per_million == 600
    assert bundle.image_pricing.quote(
        width=2560, height=1440, generated_images=1, input_images=3
    ) == 64
    assert bundle.result_downloader is not None


def test_bundle_is_frozen_from_later_settings_selection() -> None:
    first = AdapterRegistry.from_settings(cloud_settings(), configuration_version=1)
    second = AdapterRegistry.from_settings(
        Settings(
            provider_mode="cloud",
            ark_api_key="replacement-secret",
            multimodal_model_id="doubao-seed-2-1-lite-260915",
            image_model_id="doubao-seedream-5-0-pro-260628",
        ),
        configuration_version=2,
    )

    assert first.multimodal_model_id == "doubao-seed-2-1-pro-260915"
    assert first.text_pricing.input_per_million == 600
    assert second.multimodal_model_id == "doubao-seed-2-1-lite-260915"
    assert second.text_pricing.input_per_million == 80


def test_incomplete_settings_build_deterministic_bundle() -> None:
    bundle = AdapterRegistry.from_settings(Settings())

    assert bundle.mode == "deterministic"
    assert bundle.provider_name == "deterministic"
    assert bundle.multimodal_model_id == "deterministic-v1"
    assert bundle.image_model_id == "deterministic-image-v1"
