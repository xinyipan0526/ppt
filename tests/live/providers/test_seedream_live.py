"""Explicitly enabled, billable Seedream generation and editing smoke tests."""

import asyncio
from io import BytesIO
import os

from PIL import Image
import pytest

from ppt_agent.providers.image import ImageGenerationRequest, ImageInput
from ppt_agent.providers.seedream import SeedreamProvider


def _enabled() -> tuple[str, str, str]:
    if os.environ.get("LIVE_SEEDREAM_TESTS") != "1":
        pytest.skip("set LIVE_SEEDREAM_TESTS=1 to enable billable Seedream tests")
    values = tuple(os.environ.get(name, "") for name in (
        "SEEDREAM_API_KEY", "PPT_AGENT_SEEDREAM_BASE_URL", "PPT_AGENT_SEEDREAM_MODEL",
    ))
    if not all(values):
        pytest.skip("Seedream key, base URL, and model must all be configured")
    return values  # type: ignore[return-value]


@pytest.mark.live_provider
def test_seedream_generation_and_reference_edit() -> None:
    api_key, base_url, model = _enabled()

    async def scenario() -> None:
        provider = SeedreamProvider(api_key, base_url)
        generated = await provider.generate(ImageGenerationRequest(
            model=model, prompt="A minimal blue paper airplane on a white background",
            width=1024, height=1024, user_id="ppt-agent-live-contract-test",
        ))
        assert generated.content or generated.result_url
        assert generated.usage.generated_images == 1

        buffer = BytesIO()
        Image.new("RGB", (1024, 1024), "white").save(buffer, "PNG")
        edited = await provider.generate(ImageGenerationRequest(
            model=model, prompt="Preserve the supplied image composition.",
            edit_instruction="Add one small blue circle in the center.",
            width=1024, height=1024, user_id="ppt-agent-live-contract-test",
            reference_images=(ImageInput(
                "live-reference", "image/png", buffer.getvalue(), 1024, 1024,
            ),),
        ))
        assert edited.content or edited.result_url
        assert edited.usage.input_images >= 1

    asyncio.run(scenario())
