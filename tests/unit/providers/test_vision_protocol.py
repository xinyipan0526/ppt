import pytest

from ppt_agent.providers.vision import OCRRequest, VisionRequest


def test_vision_request_requires_versioned_schema_identity() -> None:
    with pytest.raises(ValueError, match="schema"):
        VisionRequest(
            model="qwen",
            image=b"image",
            media_type="image/png",
            prompt="describe",
            schema_name="",
            schema_version=1,
            json_schema={},
            max_output_tokens=100,
            user_id="user",
        )


def test_ocr_request_rejects_unbounded_output() -> None:
    with pytest.raises(ValueError, match="positive"):
        OCRRequest(
            model="qwen-ocr",
            image=b"image",
            media_type="image/png",
            max_output_tokens=0,
            user_id="user",
        )
