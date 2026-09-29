import asyncio
from io import BytesIO

from PIL import Image

from ppt_agent.providers.deterministic import (
    DeterministicImageProvider,
    DeterministicOCRProvider,
    DeterministicVisionProvider,
)
from ppt_agent.providers.image import ImageGenerationRequest
from ppt_agent.providers.vision import OCRRequest, VisionRequest


def test_deterministic_image_is_stable_valid_png() -> None:
    request = ImageGenerationRequest(
        model="deterministic-image-v1",
        prompt="blue geometric background",
        width=320,
        height=180,
        user_id="user",
    )
    provider = DeterministicImageProvider()

    first = asyncio.run(provider.generate(request))
    second = asyncio.run(provider.generate(request))

    assert first.content == second.content
    assert first.media_type == "image/png"
    assert first.usage.generated_images == 1
    with Image.open(BytesIO(first.content or b"")) as image:
        assert image.size == (320, 180)
        assert image.format == "PNG"


def test_deterministic_vision_returns_schema_shaped_result() -> None:
    request = VisionRequest(
        model="deterministic-vision-v1",
        image=b"image",
        media_type="image/png",
        prompt="locate the logo",
        schema_name="vision_analysis",
        schema_version=1,
        json_schema={"type": "object"},
        max_output_tokens=200,
        user_id="user",
    )
    result = asyncio.run(DeterministicVisionProvider().analyze(request))

    assert result.value["schema_version"] == 1
    assert result.value["summary"].startswith("Deterministic analysis")
    assert result.usage.output_tokens > 0


def test_deterministic_ocr_returns_expected_text_with_normalized_box() -> None:
    request = OCRRequest(
        model="deterministic-ocr-v1",
        image=b"image",
        media_type="image/png",
        max_output_tokens=200,
        user_id="user",
        expected_text="季度增长 25%",
    )
    result = asyncio.run(DeterministicOCRProvider().recognize(request))

    assert result.report.full_text == "季度增长 25%"
    assert result.report.spans[0].box.x == 0.1
    assert result.usage.output_tokens > 0
