import asyncio
import json

import httpx

from ppt_agent.providers.image import ImageGenerationRequest, ImageInput
from ppt_agent.providers.seedream import SeedreamProvider


def test_seedream_sends_single_image_safe_generation_request() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "image-1",
                "data": [{"url": "https://results.example/image.png", "size": "1920x1080"}],
                "usage": {"generated_images": 1, "output_tokens": 8100, "input_images": 1},
            },
        )

    reference = ImageInput("ref", "image/png", b"image", 100, 100)
    request = ImageGenerationRequest(
        model="seedream-model",
        prompt="clean background without text",
        width=1920,
        height=1080,
        user_id="user",
        reference_images=(reference,),
        edit_instruction="remove the logo",
    )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    result = asyncio.run(scenario())

    assert captured["model"] == "seedream-model"
    assert captured["size"] == "1920x1080"
    assert captured["watermark"] is False
    assert captured["sequential_image_generation"] == "disabled"
    assert "tools" not in captured
    assert captured["image"][0].startswith("data:image/png;base64,")
    assert "remove the logo" in captured["prompt"]
    assert result.result_url == "https://results.example/image.png"
    assert result.usage.generated_images == 1
