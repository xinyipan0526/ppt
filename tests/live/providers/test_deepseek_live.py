"""Opt-in smoke test against a user-configured DeepSeek account."""

import asyncio
import os

import pytest

from ppt_agent.domain.contracts import ContractModel
from ppt_agent.providers.deepseek import DeepSeekProvider
from ppt_agent.providers.text import StructuredTextRequest


class LiveAnswer(ContractModel):
    answer: str


@pytest.mark.live_provider
def test_deepseek_returns_schema_constrained_output() -> None:
    if os.environ.get("LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("set LIVE_PROVIDER_TESTS=1 to enable billable provider tests")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL")
    model = os.environ.get("DEEPSEEK_MODEL")
    if not api_key or not base_url or not model:
        pytest.skip("DeepSeek API key, base URL, and model must all be configured")

    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key,
            base_url=base_url,
            timeout_seconds=30,
        )
        result = await provider.complete(
            StructuredTextRequest(
                model=model,
                schema_name="live_answer",
                schema_version=1,
                json_schema=LiveAnswer.model_json_schema(),
                system_prompt="Return a short JSON answer matching the schema.",
                user_content="Reply with the word pong.",
                max_output_tokens=32,
                user_id="ppt-agent-live-contract-test",
            )
        )
        assert LiveAnswer.model_validate(result.value).answer
        assert result.usage.input_tokens >= 0
        assert result.usage.output_tokens > 0

    asyncio.run(scenario())
