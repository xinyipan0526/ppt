"""Opt-in, billable Ark Seedream image generation and editing smoke tests.

Seedream runs on the same Volcengine Ark platform and shares one Ark API key
with the multimodal model, so this file is gated by the same ``LIVE_ARK_TESTS=1``
switch as ``tests/live/providers/test_ark_live.py``.

Every call records the exact request body sent to Ark and the raw response body
Ark returns, then prints both to stdout for audit. Run with ``pytest -s`` (or
``--capture=no``) to see the audit output; assertions are the test conclusion.
"""

import asyncio
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from ppt_agent.providers.image import ImageGenerationRequest, ImageInput
from ppt_agent.providers.seedream import SeedreamProvider

DEFAULT_IMAGE_MODEL = "doubao-seedream-5-0-pro-260628"
_MAX_AUDIT_STRING = 500


def _enabled() -> tuple[str, str]:
    if os.environ.get("LIVE_ARK_TESTS") != "1":
        pytest.skip("set LIVE_ARK_TESTS=1 to enable billable Ark Seedream live tests")

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
        os.environ.get("PPT_AGENT_IMAGE_MODEL_ID", "").strip()
        or DEFAULT_IMAGE_MODEL
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
def test_seedream_generation_and_reference_edit() -> None:
    """Generate one image, then edit a reference image in a second call."""

    api_key, model = _enabled()
    client, calls = _audit_client(api_key)

    async def scenario() -> Any:
        async with client:
            provider = SeedreamProvider(api_key, client=client, timeout_seconds=180)
            generated = await provider.generate(
                ImageGenerationRequest(
                    model=model,
                    prompt="A minimal blue paper airplane on a white background",
                    width=1024,
                    height=1024,
                    user_id="ppt-agent-live-contract-test",
                )
            )
            reference = BytesIO()
            Image.new("RGB", (1024, 1024), "white").save(reference, "PNG")
            edited = await provider.generate(
                ImageGenerationRequest(
                    model=model,
                    prompt="Preserve the supplied image composition.",
                    edit_instruction="Add one small blue circle in the center.",
                    width=1024,
                    height=1024,
                    user_id="ppt-agent-live-contract-test",
                    reference_images=(
                        ImageInput(
                            "live-reference",
                            "image/png",
                            reference.getvalue(),
                            1024,
                            1024,
                        ),
                    ),
                )
            )
            return generated, edited

    generated, edited = asyncio.run(scenario())
    _emit("ark seedream (generation + reference edit)", calls)

    assert generated.content or generated.result_url
    assert generated.usage.generated_images == 1
    assert edited.content or edited.result_url
    assert edited.usage.input_images >= 1
    print(
        "CONCLUSION: generation "
        f"response_id={generated.response_id!r} "
        f"{generated.width}x{generated.height} "
        f"generated_images={generated.usage.generated_images}; "
        "edit "
        f"response_id={edited.response_id!r} "
        f"input_images={edited.usage.input_images}",
        flush=True,
    )
