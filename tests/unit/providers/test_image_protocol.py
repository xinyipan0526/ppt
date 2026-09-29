import pytest

from ppt_agent.providers.image import ImageGenerationRequest, ImageInput


def test_image_request_rejects_duplicate_reference_ids() -> None:
    reference = ImageInput(
        artifact_id="artifact-1",
        media_type="image/png",
        content=b"png",
        width=100,
        height=100,
    )
    with pytest.raises(ValueError, match="unique"):
        ImageGenerationRequest(
            model="model",
            prompt="draw a quiet abstract background",
            width=1920,
            height=1080,
            user_id="user",
            reference_images=(reference, reference),
        )


def test_image_input_rejects_pixel_metadata_that_exceeds_content_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        ImageInput(
            artifact_id="artifact",
            media_type="image/png",
            content=b"x",
            width=0,
            height=100,
        )
