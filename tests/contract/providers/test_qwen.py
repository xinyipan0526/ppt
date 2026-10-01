import asyncio
import json

import httpx

from ppt_agent.providers.qwen import QwenOCRProvider, QwenVisionProvider
from ppt_agent.providers.vision import OCRRequest, VisionRequest


def test_qwen_vision_uses_compatible_multimodal_schema_request() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "vision-1",
                "choices": [{"message": {"content": '{"summary":"ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    request = VisionRequest(
        model="qwen-vl",
        image=b"image",
        media_type="image/png",
        prompt="describe",
        schema_name="analysis",
        schema_version=1,
        json_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        max_output_tokens=100,
        user_id="user",
    )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await QwenVisionProvider(
                "secret", "https://workspace.example/compatible-mode/v1", client=client
            ).analyze(request)

    result = asyncio.run(scenario())
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["response_format"]["type"] == "json_schema"
    assert result.value == {"summary": "ok"}


def test_qwen_ocr_normalizes_positional_spans() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "ocr-1",
                "choices": [{"message": {"content": json.dumps({
                    "full_text": "Hello",
                    "spans": [{"text": "Hello", "box": [100, 200, 900, 300], "confidence": 0.9}],
                })}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await QwenOCRProvider(
                "secret", "https://workspace.example/compatible-mode/v1", client=client
            ).recognize(OCRRequest("qwen-ocr", b"image", "image/png", 100, "user"))

    result = asyncio.run(scenario())
    assert result.report.full_text == "Hello"
    assert result.report.spans[0].box.x == 0.1
    assert result.report.spans[0].box.width == 0.8


def test_qwen_ocr_accepts_official_plain_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "response_format" not in body
        return httpx.Response(200, json={
            "id": "ocr-text", "choices": [{"message": {"content": "Hello\n世界"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await QwenOCRProvider("secret", "https://workspace.example/compatible-mode/v1", client=client).recognize(
                OCRRequest("qwen-vl-ocr-latest", b"image", "image/png", 100, "user")
            )

    result = asyncio.run(scenario())
    assert result.report.full_text == "Hello\n世界"
    assert result.report.spans == ()
