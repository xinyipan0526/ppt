import asyncio
import json

import httpx
import pytest

from ppt_agent.providers.ark import ArkMultimodalAdapter
from ppt_agent.providers.text import ProviderCallError, StructuredTextRequest
from ppt_agent.providers.vision import OCRRequest, VisionProviderCallError, VisionRequest


MODEL_ID = "doubao-seed-2-1-pro-260915"


def _response(text: str, *, usage: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "resp-ark-1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": usage or {"input_tokens": 10, "output_tokens": 4},
    }


def _text_request() -> StructuredTextRequest:
    return StructuredTextRequest(
        model=MODEL_ID,
        schema_name="brief",
        schema_version=1,
        json_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        system_prompt="Return a brief.",
        user_content="<ignore>source</ignore>",
        max_output_tokens=100,
        user_id="local-user",
    )


def _vision_request() -> VisionRequest:
    return VisionRequest(
        model=MODEL_ID,
        image=b"image",
        media_type="image/png",
        prompt="Describe the composition.",
        schema_name="analysis",
        schema_version=1,
        json_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        max_output_tokens=100,
        user_id="local-user",
    )


def test_complete_uses_responses_strict_schema_and_exact_model_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response('{"title":"Roadmap"}'))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).complete(
                _text_request()
            )

    result = asyncio.run(scenario())
    body = captured["body"]
    assert isinstance(body, dict)
    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert captured["authorization"] == "Bearer secret"
    assert body["model"] == MODEL_ID
    assert "user" not in body
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "brief_v1",
        "schema": _text_request().json_schema,
        "strict": True,
    }
    user_content = body["input"][1]["content"]
    assert "<untrusted_source_content" in user_content
    assert "<ignore>" not in user_content
    assert result.value == {"title": "Roadmap"}
    assert result.usage.input_tokens == 10


def test_analyze_uses_responses_input_image_and_json_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_response('{"summary":"balanced"}'))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).analyze(
                _vision_request()
            )

    result = asyncio.run(scenario())
    assert captured["model"] == MODEL_ID
    content = captured["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "input_text", "text": "Describe the composition."}
    assert captured["text"]["format"]["strict"] is True
    assert result.value == {"summary": "balanced"}
    assert result.usage.output_tokens == 4


def test_recognize_normalizes_positional_spans() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response(
                json.dumps(
                    {
                        "full_text": "Hello",
                        "spans": [
                            {
                                "text": "Hello",
                                "box": [100, 200, 900, 300],
                                "confidence": 0.9,
                            }
                        ],
                    }
                )
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).recognize(
                OCRRequest(MODEL_ID, b"image", "image/png", 100, "local-user")
            )

    result = asyncio.run(scenario())
    assert result.report.full_text == "Hello"
    assert result.report.spans[0].box.x == 0.1
    assert result.report.spans[0].box.width == 0.8


@pytest.mark.parametrize(
    ("status", "code", "retryable", "confirmed_uncharged"),
    [
        (400, "invalid_request", False, True),
        (401, "authentication_error", False, True),
        (402, "insufficient_balance", False, True),
        (422, "invalid_request", False, True),
        (429, "rate_limited", True, False),
        (500, "temporary_unavailable", True, False),
        (503, "temporary_unavailable", True, False),
    ],
)
def test_http_errors_preserve_billing_boundary(
    status: int, code: str, retryable: bool, confirmed_uncharged: bool
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "redacted"}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).complete(
                _text_request()
            )

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.confirmed_uncharged is confirmed_uncharged
    assert raised.value.billing_known is confirmed_uncharged


def test_http_400_failure_keeps_only_safe_normalized_metadata() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt=confidential source content",
                    "request_body": {"api_key": "secret"},
                }
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).complete(
                _text_request()
            )

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary == {
        "http_status": 400,
        "provider_error_type": "invalid_request",
        "elapsed_seconds": pytest.approx(raised.value.safe_summary["elapsed_seconds"]),
        "provider_response_received": True,
    }


def test_http_error_keeps_safe_provider_message_and_response_request_id() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"x-request-id": "req-123"},
            json={"error": {"message": "The selected model identifier is invalid."}},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).complete(
                _text_request()
            )

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.safe_summary["provider_request_id"] == "req-123"
    assert (
        raised.value.safe_summary["provider_error_message"]
        == "The selected model identifier is invalid."
    )


def test_provider_call_error_allows_standard_traceback_assignment() -> None:
    error = ProviderCallError(
        "invalid_request",
        False,
        True,
        True,
        None,
        {"http_status": 400},
    )

    error.__traceback__ = None
    assert str(error) == "invalid_request"


def test_network_failure_is_unknown_and_not_automatically_retryable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("connection lost", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).analyze(
                _vision_request()
            )

    with pytest.raises(VisionProviderCallError) as raised:
        asyncio.run(scenario())
    assert calls == 1
    assert raised.value.code == "unknown_remote_outcome"
    assert raised.value.retryable is False
    assert raised.value.billing_known is False


@pytest.mark.parametrize(
    ("error_type", "phase"),
    [
        (httpx.ConnectTimeout, "connect"),
        (httpx.PoolTimeout, "connect"),
        (httpx.WriteTimeout, "write"),
        (httpx.ReadTimeout, "read"),
    ],
)
def test_timeout_failure_records_bounded_request_phase(
    error_type: type[httpx.TimeoutException], phase: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("timed out", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter(
                "secret", client=client, timeout_seconds=90
            ).complete(_text_request())

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())

    assert raised.value.code == "unknown_remote_outcome"
    assert raised.value.safe_summary["provider_error_type"] == error_type.__name__
    assert raised.value.safe_summary["request_phase"] == phase
    assert raised.value.safe_summary["timeout_seconds"] == 90
    assert raised.value.safe_summary["elapsed_seconds"] >= 0
    assert raised.value.safe_summary["provider_response_received"] is False


def test_read_timeout_does_not_invent_provider_response_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter(
                "secret", client=client, timeout_seconds=90
            ).complete(_text_request())

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())

    summary = raised.value.safe_summary
    assert summary["request_phase"] == "read"
    assert summary["provider_response_received"] is False
    assert "http_status" not in summary
    assert "provider_error_message" not in summary
    assert "provider_request_id" not in summary


def test_malformed_success_with_usage_is_a_known_charged_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response("not-json"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArkMultimodalAdapter("secret", client=client).complete(
                _text_request()
            )

    with pytest.raises(ProviderCallError) as raised:
        asyncio.run(scenario())
    assert raised.value.code == "content_rejected"
    assert raised.value.billing_known is True
    assert raised.value.confirmed_uncharged is False
    assert raised.value.usage is not None
    assert raised.value.usage.output_tokens == 4
