import pytest

from ppt_agent.providers.catalog import DEFAULT_MODEL_CATALOG
from ppt_agent.providers.capabilities import (
    ModelCapability,
    ModelCategory,
    ModelLifecycle,
    UnsupportedProviderCapability,
)


def test_catalog_exposes_exact_ark_model_ids_and_pricing() -> None:
    pro = DEFAULT_MODEL_CATALOG.require(
        "doubao-seed-2-1-pro-260915", ModelCategory.MULTIMODAL
    )
    lite = DEFAULT_MODEL_CATALOG.require(
        "doubao-seed-2-1-lite-260915", ModelCategory.MULTIMODAL
    )
    image = DEFAULT_MODEL_CATALOG.require(
        "doubao-seedream-5-0-pro-260628", ModelCategory.IMAGE
    )

    assert pro.model_id == "doubao-seed-2-1-pro-260915"
    assert pro.display_name == "Doubao Seed 2.1 Pro"
    assert pro.recommended is True
    assert pro.lifecycle is ModelLifecycle.STABLE
    assert pro.capabilities == frozenset(
        {
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.IMAGE_UNDERSTANDING,
            ModelCapability.OCR,
            ModelCapability.REGION_LOCATION,
        }
    )
    assert (pro.token_pricing.input_per_million, pro.token_pricing.output_per_million) == (
        600,
        3000,
    )
    assert (lite.token_pricing.input_per_million, lite.token_pricing.output_per_million) == (
        80,
        270,
    )
    assert image.model_id == "doubao-seedream-5-0-pro-260628"
    assert image.capabilities == frozenset(
        {ModelCapability.IMAGE_GENERATION, ModelCapability.IMAGE_EDITING}
    )
    assert image.image_pricing.output_up_to_2_61mp == 30
    assert image.image_pricing.output_above_2_61mp == 60
    assert image.image_pricing.additional_reference_image == 2
    assert image.result_hosts == (
        "ark-content-generation-cn-beijing.tos-cn-beijing.volces.com",
    )


def test_catalog_rejects_wrong_category_and_experimental_selection() -> None:
    with pytest.raises(UnsupportedProviderCapability, match="category"):
        DEFAULT_MODEL_CATALOG.require(
            "doubao-seedream-5-0-pro-260628", ModelCategory.MULTIMODAL
        )
    with pytest.raises(UnsupportedProviderCapability, match="experimental"):
        DEFAULT_MODEL_CATALOG.require(
            "deepseek-v4-1-flash-260910", ModelCategory.MULTIMODAL
        )

    experimental = DEFAULT_MODEL_CATALOG.require(
        "deepseek-v4-1-flash-260910",
        ModelCategory.MULTIMODAL,
        allow_experimental=True,
    )
    assert experimental.lifecycle is ModelLifecycle.EXPERIMENTAL
    assert (
        experimental.token_pricing.input_per_million,
        experimental.token_pricing.output_per_million,
    ) == (150, 450)


@pytest.mark.parametrize(
    "invalid",
    [
        "Doubao Seed 2.1 Pro",
        "doubao-seed-2.1-pro",
        "DOUBAO-SEED-2-1-PRO-260915",
        "ark.doubao-seed-2-1-pro.260915",
    ],
)
def test_catalog_rejects_labels_aliases_and_case_variants(invalid: str) -> None:
    with pytest.raises(KeyError, match="unsupported model id"):
        DEFAULT_MODEL_CATALOG.require(invalid, ModelCategory.MULTIMODAL)


def test_public_catalog_snapshot_keeps_model_id_as_external_identity() -> None:
    snapshot = DEFAULT_MODEL_CATALOG.public_snapshot()

    assert [item["model_id"] for item in snapshot] == [
        "doubao-seed-2-1-pro-260915",
        "doubao-seed-2-1-lite-260915",
        "deepseek-v4-1-flash-260910",
        "doubao-seedream-5-0-pro-260628",
    ]
    assert all(item["adapter_id"] == "volcengine_ark" for item in snapshot)
    assert all("catalog_id" not in item for item in snapshot)
    assert all(item["pricing_version"] == "official-cn-2026-10-03" for item in snapshot)


def test_catalog_rejects_duplicate_model_ids() -> None:
    from ppt_agent.providers.catalog import ModelCatalog

    entry = DEFAULT_MODEL_CATALOG.require(
        "doubao-seed-2-1-pro-260915", ModelCategory.MULTIMODAL
    )
    with pytest.raises(ValueError, match="duplicate model id"):
        ModelCatalog((entry, entry))
