import pytest

from ppt_agent.operations.image_runner import ImagePricing
from ppt_agent.providers.catalog import DEFAULT_MODEL_CATALOG
from ppt_agent.providers.capabilities import ModelCategory
from ppt_agent.providers.image import ImageGenerationRequest, ImageInput


def pricing() -> ImagePricing:
    entry = DEFAULT_MODEL_CATALOG.require(
        "doubao-seedream-5-0-pro-260628", ModelCategory.IMAGE
    )
    assert entry.image_pricing is not None
    return ImagePricing.from_schedule(entry.image_pricing)


def test_catalog_schedule_quotes_pixel_tiers_at_exact_boundary() -> None:
    value = pricing()
    assert value.quote(
        width=1450, height=1800, generated_images=1, input_images=0
    ) == 30
    assert value.quote(
        width=1451, height=1800, generated_images=1, input_images=0
    ) == 60


def test_first_reference_is_free_and_each_additional_reference_is_charged() -> None:
    value = pricing()
    assert value.quote(
        width=1920, height=1080, generated_images=1, input_images=0
    ) == 30
    assert value.quote(
        width=1920, height=1080, generated_images=1, input_images=1
    ) == 30
    assert value.quote(
        width=1920, height=1080, generated_images=1, input_images=3
    ) == 34


def test_reserve_accounts_for_output_size_and_all_reference_images() -> None:
    references = tuple(
        ImageInput(f"ref-{index}", "image/png", b"image", 100, 100)
        for index in range(3)
    )
    request = ImageGenerationRequest(
        "doubao-seedream-5-0-pro-260628",
        "background",
        2560,
        1440,
        "user",
        reference_images=references,
    )
    assert pricing().reserve(request) == 64


@pytest.mark.parametrize(
    ("width", "height", "generated_images", "input_images"),
    [(0, 100, 1, 0), (100, 0, 1, 0), (100, 100, 0, 0), (100, 100, 1, -1)],
)
def test_quote_rejects_invalid_usage(
    width: int, height: int, generated_images: int, input_images: int
) -> None:
    with pytest.raises(ValueError):
        pricing().quote(
            width=width,
            height=height,
            generated_images=generated_images,
            input_images=input_images,
        )
