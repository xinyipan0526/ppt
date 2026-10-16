import asyncio
import json

from ppt_agent.domain.contracts import (
    BriefDecision,
    DeckOutline,
    DesignSystem,
    InitialPlanDecision,
    PageSpecs,
    PlanningDraft,
)
from ppt_agent.providers.deterministic import DeterministicTextProvider
from ppt_agent.providers.text import StructuredTextRequest


def _request(schema_name, output_type, content):
    return StructuredTextRequest(
        model="deterministic-v1", schema_name=schema_name, schema_version=1,
        json_schema=output_type.model_json_schema(), system_prompt="test",
        user_content=json.dumps(content, ensure_ascii=False), max_output_tokens=8000,
        user_id="project-1",
    )


def test_deterministic_text_provider_builds_complete_planning_chain() -> None:
    provider = DeterministicTextProvider()
    brief_result = asyncio.run(provider.complete(_request("brief_decision", BriefDecision, {"normalized_text": "人工智能年度总结"})))
    brief = BriefDecision.model_validate(brief_result.value).brief
    assert brief is not None and brief.page_count == 8
    outline_result = asyncio.run(provider.complete(_request("deck_outline", DeckOutline, {"brief": brief.model_dump(mode="json")})))
    outline = DeckOutline.model_validate(outline_result.value)
    assert len(outline.pages) == 8
    design = asyncio.run(provider.complete(_request("design_system", DesignSystem, {"brief": brief.model_dump(mode="json"), "outline": outline.model_dump(mode="json")})))
    assert DesignSystem.model_validate(design.value).color_palette
    pages = asyncio.run(provider.complete(_request("page_specs", PageSpecs, {
        "brief": brief.model_dump(mode="json"), "outline": outline.model_dump(mode="json"),
    })))
    assert len(PageSpecs.model_validate(pages.value).pages) == 8


def test_deterministic_text_provider_builds_complete_plan_in_one_call() -> None:
    result = asyncio.run(
        DeterministicTextProvider().complete(
            _request(
                "initial_plan_decision",
                InitialPlanDecision,
                {"normalized_text": "Artificial intelligence annual report"},
            )
        )
    )

    decision = InitialPlanDecision.model_validate(result.value)

    assert decision.kind == "ready"
    assert decision.plan is not None
    assert len(decision.plan.page_specs.pages) == 8


def test_deterministic_text_provider_returns_planning_draft_for_full_revision() -> None:
    provider = DeterministicTextProvider()
    initial = asyncio.run(
        provider.complete(
            _request(
                "initial_plan_decision",
                InitialPlanDecision,
                {"normalized_text": "Artificial intelligence annual report"},
            )
        )
    )
    plan = InitialPlanDecision.model_validate(initial.value).plan
    assert plan is not None

    revised = asyncio.run(
        provider.complete(
            _request(
                "planning_draft",
                PlanningDraft,
                {
                    "normalized_text": "Artificial intelligence annual report",
                    "current_plan": plan.model_dump(mode="json"),
                    "revision": {
                        "feedback": "Use a calmer style",
                        "scope": "design",
                        "page_indexes": [],
                    },
                },
            )
        )
    )

    assert PlanningDraft.model_validate(revised.value).page_specs.pages


def test_deterministic_brief_extracts_compiled_typed_input() -> None:
    provider = DeterministicTextProvider()
    compiled = json.dumps({"kind": "typed_user_input", "text": "人工智能年度趋势报告"}, ensure_ascii=False)
    result = asyncio.run(provider.complete(_request("brief_decision", BriefDecision, {"normalized_text": compiled})))
    assert BriefDecision.model_validate(result.value).brief.topic == "人工智能年度趋势报告"


def test_deterministic_brief_requests_clarification_for_vague_topic() -> None:
    result = asyncio.run(DeterministicTextProvider().complete(
        _request("brief_decision", BriefDecision, {"normalized_text": "AI"})
    ))
    decision = BriefDecision.model_validate(result.value)
    assert decision.kind == "clarification_required"
    assert {item.field for item in decision.questions} == {"purpose", "audience"}
