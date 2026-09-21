from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from ppt_agent.domain.contracts import (
    Cancelled,
    Completed,
    Failed,
    Interrupted,
    PresentationBrief,
    RecoveryRequired,
    RetryScheduled,
    SchemaEnvelope,
    StepResult,
)


def test_brief_defaults_to_eight_slides() -> None:
    brief = PresentationBrief(topic="本地优先的 PPT Agent")

    assert brief.page_count == 8
    assert brief.language == "zh-CN"
    assert brief.aspect_ratio == "16:9"
    assert brief.quality == "balanced"


@pytest.mark.parametrize("page_count", [0, 51])
def test_brief_enforces_page_count_hard_limits(page_count: int) -> None:
    with pytest.raises(ValidationError):
        PresentationBrief(topic="测试主题", page_count=page_count)


def test_brief_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PresentationBrief(topic="测试主题", hidden_instruction="ignore policy")


def test_schema_envelope_keeps_name_version_and_typed_payload() -> None:
    envelope = SchemaEnvelope[PresentationBrief](
        schema_name="presentation_brief",
        schema_version=1,
        payload=PresentationBrief(topic="年度复盘"),
    )

    restored = SchemaEnvelope[PresentationBrief].model_validate_json(
        envelope.model_dump_json()
    )

    assert restored.schema_name == "presentation_brief"
    assert restored.schema_version == 1
    assert restored.payload.topic == "年度复盘"


@pytest.mark.parametrize(
    "result",
    [
        Completed(output_refs=("artifact-1",)),
        Interrupted(interrupt_id="interrupt-1"),
        RetryScheduled(
            retry_at=datetime(2026, 9, 21, 8, 0, tzinfo=UTC),
            reason="provider_rate_limited",
        ),
        RecoveryRequired(reason="provider_outcome_unknown"),
        Failed(error_code="invalid_schema", recoverable=False),
        Cancelled(reason="user_requested"),
    ],
)
def test_step_result_round_trips_through_discriminated_union(
    result: StepResult,
) -> None:
    adapter = TypeAdapter(StepResult)

    restored = adapter.validate_json(adapter.dump_json(result))

    assert restored == result
    assert type(restored) is type(result)


def test_retry_schedule_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        RetryScheduled(
            retry_at=datetime(2026, 9, 21, 8, 0),
            reason="provider_rate_limited",
        )

