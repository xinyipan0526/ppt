"""Opt-in, billable Ark live smoke tests covering the full multimodal surface.

These run against the real Volcengine Ark endpoint only when ``LIVE_ARK_TESTS=1``
is set (and an Ark key is available), so they can never be billed accidentally.

Every call records the exact request body sent to Ark and the raw response body
Ark returns, then prints both to stdout for audit. Run with ``pytest -s`` (or
``--capture=no``) to see the audit output; assertions are the test conclusion.

Coverage:
  * ``complete``   -- strict structured text output  (Doubao Seed 2.1)
  * ``analyze``    -- vision understanding           (Doubao Seed 2.1)
  * ``recognize``  -- OCR with positional spans      (Doubao Seed 2.1)

Image generation (Seedream) is covered in ``tests/live/providers/test_seedream_live.py``
and is gated by the same ``LIVE_ARK_TESTS`` switch because it shares one Ark key.
"""

import asyncio
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from PIL import Image, ImageDraw

from ppt_agent.domain.contracts import ContractModel
from ppt_agent.providers.ark import ArkMultimodalAdapter
from ppt_agent.providers.text import StructuredTextRequest
from ppt_agent.providers.vision import OCRRequest, VisionRequest

DEFAULT_MULTIMODAL_MODEL = "doubao-seed-2-1-pro-260915"
_MAX_AUDIT_STRING = 500


class ConnectivityAnswer(ContractModel):
    answer: Literal["pong"]


class VisionAnswer(ContractModel):
    summary: str


def _enabled() -> tuple[str, str]:
    if os.environ.get("LIVE_ARK_TESTS") != "1":
        pytest.skip("set LIVE_ARK_TESTS=1 to enable billable Ark live tests")

    api_key = os.environ.get("ARK_API_KEY", "").strip()
    key_file = os.environ.get("ARK_API_KEY_FILE", "").strip()
    if not api_key and key_file:
        try:
            api_key = Path(key_file).read_text(encoding="utf-8").strip()
        except OSError:
            pytest.skip("ARK_API_KEY_FILE must point to a readable Ark key file")
    if not api_key:
        pytest.skip("provide an Ark key via ARK_API_KEY or ARK_API_KEY_FILE")

    model = (
        os.environ.get("PPT_AGENT_MULTIMODAL_MODEL_ID", "").strip()
        or DEFAULT_MULTIMODAL_MODEL
    )
    return api_key, model


def _redact(value: Any, api_key: str) -> Any:
    """Return a copy of ``value`` safe to print: no key, no giant base64 blobs."""
    if isinstance(value, str):
        if api_key and api_key in value:
            value = value.replace(api_key, "***")
        if len(value) > _MAX_AUDIT_STRING:
            return value[:120] + f"...<truncated {len(value)} chars>"
        return value
    if isinstance(value, dict):
        return {str(key): _redact(item, api_key) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    return value


def _decode(raw: bytes, api_key: str) -> Any:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")
    return _redact(value, api_key)


def _audit_client(api_key: str) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    """Build an httpx client that records each request body and response body."""
    calls: list[dict[str, Any]] = []

    async def on_request(request: httpx.Request) -> None:
        calls.append({
            "method": request.method,
            "url": str(request.url),
            "request_body": _decode(request.content, api_key),
        })

    async def on_response(response: httpx.Response) -> None:
        await response.aread()
        calls[-1]["status"] = response.status_code
        calls[-1]["response_body"] = _decode(response.content, api_key)

    client = httpx.AsyncClient(
        event_hooks={"request": [on_request], "response": [on_response]},
    )
    return client, calls


def _emit(name: str, calls: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 80, flush=True)
    print(f"AUDIT: {name}", flush=True)
    for index, call in enumerate(calls, 1):
        print("-" * 80, flush=True)
        print(f"[call {index}] {call['method']} {call['url']}", flush=True)
        print("-- request body (sent to Ark) --", flush=True)
        print(json.dumps(call["request_body"], ensure_ascii=False, indent=2), flush=True)
        print(f"-- response body (HTTP {call.get('status')}) --", flush=True)
        print(
            json.dumps(call.get("response_body"), ensure_ascii=False, indent=2),
            flush=True,
        )
    print("=" * 80, flush=True)


@pytest.mark.live_provider
def test_ark_text_connectivity_with_structured_output() -> None:
    """One real, minimal structured Responses API text request."""

    api_key, model = _enabled()
    client, calls = _audit_client(api_key)

    async def scenario() -> Any:
        async with client:
            provider = ArkMultimodalAdapter(
                api_key, client=client, timeout_seconds=30
            )
            return await provider.complete(
                StructuredTextRequest(
                    model=model,
                    schema_name="connectivity_answer",
                    schema_version=1,
                    json_schema=ConnectivityAnswer.model_json_schema(),
                    system_prompt="Return the JSON object requested by the schema.",
                    user_content="Reply with exactly the word pong.",
                    max_output_tokens=128,
                    user_id="ppt-agent-ark-connectivity-test",
                )
            )

    result = asyncio.run(scenario())
    _emit("ark complete (structured text)", calls)
    answer = ConnectivityAnswer.model_validate(result.value)
    assert answer.answer == "pong"
    assert result.usage.output_tokens > 0
    print(
        f"CONCLUSION: structured output {result.value!r}; "
        f"input_tokens={result.usage.input_tokens} "
        f"output_tokens={result.usage.output_tokens}",
        flush=True,
    )


@pytest.mark.live_provider
def test_ark_vision_analysis() -> None:
    """One real vision-understanding request against a rendered test image."""

    api_key, model = _enabled()
    image = Image.new("RGB", (512, 256), "white")
    ImageDraw.Draw(image).text((40, 100), "PPT AGENT", fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    content = buffer.getvalue()

    client, calls = _audit_client(api_key)

    async def scenario() -> Any:
        async with client:
            provider = ArkMultimodalAdapter(
                api_key, client=client, timeout_seconds=30
            )
            return await provider.analyze(
                VisionRequest(
                    model=model,
                    image=content,
                    media_type="image/png",
                    prompt=(
                        "Describe the composition of this image and return the "
                        "required JSON schema."
                    ),
                    schema_name="vision_answer",
                    schema_version=1,
                    json_schema=VisionAnswer.model_json_schema(),
                    max_output_tokens=256,
                    user_id="ppt-agent-ark-vision-test",
                )
            )

    result = asyncio.run(scenario())
    _emit("ark analyze (vision understanding)", calls)
    VisionAnswer.model_validate(result.value)
    assert result.value["summary"].strip()
    assert result.usage.output_tokens > 0
    print(
        f"CONCLUSION: vision summary {result.value['summary']!r}; "
        f"output_tokens={result.usage.output_tokens}",
        flush=True,
    )


@pytest.mark.live_provider
def test_ark_ocr() -> None:
    """One real OCR request against a rendered text image."""

    api_key, model = _enabled()
    image = Image.new("RGB", (512, 256), "white")
    ImageDraw.Draw(image).text((40, 100), "PPT AGENT 2026", fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    content = buffer.getvalue()

    client, calls = _audit_client(api_key)

    async def scenario() -> Any:
        async with client:
            provider = ArkMultimodalAdapter(
                api_key, client=client, timeout_seconds=30
            )
            return await provider.recognize(
                OCRRequest(
                    model=model,
                    image=content,
                    media_type="image/png",
                    max_output_tokens=256,
                    user_id="ppt-agent-ark-ocr-test",
                    expected_text="PPT AGENT 2026",
                )
            )

    result = asyncio.run(scenario())
    _emit("ark recognize (OCR)", calls)
    assert "PPT" in result.report.full_text.upper()
    assert result.usage.output_tokens > 0
    print(
        f"CONCLUSION: OCR full_text {result.report.full_text!r}; "
        f"spans={len(result.report.spans)} "
        f"output_tokens={result.usage.output_tokens}",
        flush=True,
    )
