"""Explicitly enabled, billable Qwen vision and OCR smoke tests."""

import asyncio
from io import BytesIO
import os

from PIL import Image, ImageDraw
import pytest

from ppt_agent.domain.vision import VisionAnalysisV1
from ppt_agent.providers.qwen import QwenOCRProvider, QwenVisionProvider
from ppt_agent.providers.vision import OCRRequest, VisionRequest


def _enabled() -> tuple[str, str, str, str]:
    if os.environ.get("LIVE_QWEN_TESTS") != "1":
        pytest.skip("set LIVE_QWEN_TESTS=1 to enable billable Qwen tests")
    values = tuple(os.environ.get(name, "") for name in (
        "QWEN_API_KEY", "PPT_AGENT_QWEN_BASE_URL",
        "PPT_AGENT_QWEN_VISION_MODEL", "PPT_AGENT_QWEN_OCR_MODEL",
    ))
    if not all(values):
        pytest.skip("Qwen key, base URL, vision model, and OCR model must be configured")
    return values  # type: ignore[return-value]


@pytest.mark.live_provider
def test_qwen_vision_and_ocr() -> None:
    api_key, base_url, vision_model, ocr_model = _enabled()
    image = Image.new("RGB", (512, 256), "white")
    ImageDraw.Draw(image).text((40, 100), "PPT AGENT 2026", fill="black")
    buffer = BytesIO(); image.save(buffer, "PNG")
    content = buffer.getvalue()

    async def scenario() -> None:
        vision = await QwenVisionProvider(api_key, base_url).analyze(VisionRequest(
            model=vision_model, image=content, media_type="image/png",
            prompt="Describe this image and return the required JSON schema.",
            schema_name="vision_analysis", schema_version=1,
            json_schema=VisionAnalysisV1.model_json_schema(), max_output_tokens=512,
            user_id="ppt-agent-live-contract-test",
        ))
        VisionAnalysisV1.model_validate(vision.value)
        ocr = await QwenOCRProvider(api_key, base_url).recognize(OCRRequest(
            model=ocr_model, image=content, media_type="image/png",
            max_output_tokens=128, user_id="ppt-agent-live-contract-test",
            expected_text="PPT AGENT 2026",
        ))
        assert "PPT" in ocr.report.full_text.upper()

    asyncio.run(scenario())
