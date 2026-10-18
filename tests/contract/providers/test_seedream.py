import asyncio
import json
import math

import httpx
import pytest

from ppt_agent.providers.image import (
    ImageGenerationRequest,
    ImageInput,
    ImageProviderCallError,
)
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
        model="doubao-seedream-5-0-pro-260628",
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

    assert captured["model"] == "doubao-seedream-5-0-pro-260628"
    assert captured["size"] == "1920x1080"
    assert captured["watermark"] is False
    assert "sequential_image_generation" not in captured
    assert captured["response_format"] == "url"
    assert "tools" not in captured
    assert captured["image"][0].startswith("data:image/png;base64,")
    assert "remove the logo" in captured["prompt"]
    assert result.result_url == "https://results.example/image.png"
    assert result.usage.generated_images == 1


def test_seedream_scales_small_layout_box_to_supported_output_size() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "image-small-layout",
                "data": [
                    {
                        "url": "https://results.example/image.png",
                        "size": captured["size"],
                    }
                ],
                "usage": {"generated_images": 1},
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="presentation hero visual",
        width=838,
        height=960,
        user_id="user",
    )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    result = asyncio.run(scenario())

    output_width, output_height = (
        int(value) for value in captured["size"].split("x")
    )
    assert output_width * output_height >= 921_600
    assert math.isclose(
        output_width / output_height,
        request.width / request.height,
        rel_tol=0.002,
    )
    assert (result.width, result.height) == (output_width, output_height)


def test_seedream_http_error_exposes_message_without_request_secrets() -> None:
    api_key = "secret-api-key-value"
    prompt = "confidential   launch\nillustration"
    edit_instruction = "remove   private\nlogo"
    image_data = "data:image/png;base64,aGVsbG8="

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"x-request-id": "021791956223546b9f489096afc36acf45c00ec3935d23a419787"},
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": (
                        "Invalid image request for "
                        "confidential launch illustration; edit=remove private logo; "
                        f"credential={api_key}; image={image_data}"
                    ),
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt=prompt,
        width=1920,
        height=1080,
        user_id="user",
        reference_images=(ImageInput("ref", "image/png", b"image", 10, 10),),
        edit_instruction=edit_instruction,
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                api_key, "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary == {
        "http_status": 400,
        "provider_error_type": "invalid_request",
        "provider_error_code": "InvalidParameter",
        "provider_request_id": "021791956223546b9f489096afc36acf45c00ec3935d23a419787",
        "provider_error_message": "[REDACTED]",
    }
    assert "confidential launch illustration" not in str(raised.value.safe_summary)
    assert "remove private logo" not in str(raised.value.safe_summary)
    assert api_key not in str(raised.value.safe_summary)
    assert image_data not in str(raised.value.safe_summary)


def test_seedream_http_error_hides_wrapped_bare_reference_image_data() -> None:
    encoded_reference = "MDEyMzQ1Njc4 OWFiY2RlZg=="

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": f"Rejected image bytes {encoded_reference}",
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="safe prompt",
        width=1920,
        height=1080,
        user_id="user",
        reference_images=(
            ImageInput("ref", "image/png", b"0123456789abcdef", 10, 10),
        ),
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    message = raised.value.safe_summary["provider_error_message"]
    assert message == "[REDACTED]"
    assert "MDEyMzQ1Njc4" not in message


def test_seedream_extracts_request_id_from_provider_error_message() -> None:
    request_id = "021791956223546b9f489096afc36acf45c00ec3935d23a419787"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": (
                        "The parameter `size` is not valid. "
                        f"Request id: {request_id}"
                    ),
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="safe prompt",
        width=1920,
        height=1080,
        user_id="user",
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary["provider_request_id"] == request_id


def test_seedream_redacts_partial_case_changed_prompt_echo() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": "Rejected word: aurora",
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="make a slide with AURORA theme",
        width=1920,
        height=1080,
        user_id="user",
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary["provider_error_message"] == "[REDACTED]"


def test_seedream_does_not_treat_prompt_echo_as_request_id() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": "Invalid prompt: Request id: PRIVATE-CODENAME",
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="make a slide Request id: PRIVATE-CODENAME about roadmap",
        width=1920,
        height=1080,
        user_id="user",
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary["provider_error_message"] == "[REDACTED]"
    assert "provider_request_id" not in raised.value.safe_summary


def test_seedream_keeps_request_id_header_for_non_json_error() -> None:
    request_id = "request-from-upstream-proxy-123"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"x-request-id": request_id},
            text="upstream temporarily unavailable",
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="safe prompt",
        width=1920,
        height=1080,
        user_id="user",
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                "secret", "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary["provider_request_id"] == request_id


def test_seedream_does_not_log_sensitive_provider_error_code() -> None:
    api_key = "secret-api-key-value"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": api_key,
                    "message": "The request was rejected.",
                }
            },
        )

    request = ImageGenerationRequest(
        model="doubao-seedream-5-0-pro-260628",
        prompt="safe prompt",
        width=1920,
        height=1080,
        user_id="user",
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await SeedreamProvider(
                api_key, "https://ark.example/api/v3", client=client
            ).generate(request)

    with pytest.raises(ImageProviderCallError) as raised:
        asyncio.run(scenario())

    assert "provider_error_code" not in raised.value.safe_summary
    assert api_key not in str(raised.value.safe_summary)
