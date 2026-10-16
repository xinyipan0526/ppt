import pytest
from pydantic import ValidationError

from ppt_agent.domain.contracts import (
    BriefDecision,
    DeckOutline,
    DesignSystem,
    InitialPlanDecision,
    OutlinePage,
    PageSpec,
    PageSpecs,
    PlanningDraft,
    PresentationBrief,
)


def _outline(count: int = 8) -> DeckOutline:
    return DeckOutline(
        narrative="A clear narrative",
        pages=[
            OutlinePage(page_index=index, objective=f"p{index}")
            for index in range(1, count + 1)
        ],
    )


def _pages(count: int = 8, *, outline: DeckOutline | None = None) -> PageSpecs:
    return PageSpecs(
        brief_page_count=count,
        outline=outline or _outline(count),
        pages=[
            PageSpec(page_index=index, objective=f"p{index}", page_type="content")
            for index in range(1, count + 1)
        ],
    )


def _design_system() -> DesignSystem:
    return DesignSystem(
        color_palette=("#101820", "#F2AA4C"),
        typography={"heading": "Inter", "body": "Noto Sans SC"},
        visual_style="Editorial clarity",
        image_style="Clean geometric illustration",
        spacing_scale=(8, 16, 24),
    )


def _draft(count: int = 8) -> PlanningDraft:
    outline = _outline(count)
    return PlanningDraft(
        brief=PresentationBrief(topic="Roadmap", page_count=count),
        outline=outline,
        design_system=_design_system(),
        page_specs=_pages(count, outline=outline),
    )


def test_planning_contracts_accept_continuous_matching_pages() -> None:
    brief = PresentationBrief(topic="Roadmap", page_count=8)
    pages = _pages(brief.page_count)

    assert len(pages.pages) == brief.page_count


@pytest.mark.parametrize(
    "indexes",
    ([1, 1], [1, 3]),
)
def test_outline_rejects_duplicate_or_gapped_indexes(indexes: list[int]) -> None:
    with pytest.raises(ValidationError, match="continuous"):
        DeckOutline(
            narrative="Narrative",
            pages=[OutlinePage(page_index=i, objective=f"p{i}") for i in indexes],
        )


def test_page_specs_reject_count_and_objective_mismatch() -> None:
    with pytest.raises(ValidationError, match="brief page count"):
        _pages(7, outline=_outline(8))

    with pytest.raises(ValidationError, match="objective"):
        PageSpecs(
            brief_page_count=2,
            outline=_outline(2),
            pages=[
                PageSpec(page_index=1, objective="p1", page_type="cover"),
                PageSpec(page_index=2, objective="different", page_type="content"),
            ],
        )


def test_planning_contracts_are_strict_and_page_count_is_bounded() -> None:
    with pytest.raises(ValidationError, match="extra"):
        OutlinePage(page_index=1, objective="p1", unknown=True)
    with pytest.raises(ValidationError):
        PresentationBrief(topic="Too long", page_count=51)


def test_brief_decision_normalizes_fields_from_the_inactive_branch() -> None:
    ready = BriefDecision.model_validate(
        {
            "kind": "ready",
            "brief": {"topic": "北京"},
            "reason": "The source is sufficiently detailed.",
            "questions": [],
        }
    )
    clarification = BriefDecision.model_validate(
        {
            "kind": "clarification_required",
            "brief": {"topic": "ignored"},
            "reason": "需要确认受众",
            "questions": [
                {
                    "field": "audience",
                    "question": "主要受众是谁？",
                    "reason": "受众影响内容深度",
                    "input_type": "text",
                }
            ],
        }
    )

    assert ready.reason is None
    assert ready.questions == ()
    assert clarification.brief is None


def test_initial_plan_decision_accepts_one_consistent_ready_plan() -> None:
    decision = InitialPlanDecision(kind="ready", plan=_draft())

    restored = InitialPlanDecision.model_validate(decision.model_dump(mode="json"))

    assert restored == decision
    assert restored.plan is not None
    assert len(restored.plan.page_specs.pages) == 8


def test_initial_plan_decision_normalizes_inactive_branch() -> None:
    ready = InitialPlanDecision.model_validate(
        {
            "kind": "ready",
            "plan": _draft().model_dump(mode="json"),
            "reason": "ignored",
            "questions": [
                {
                    "field": "audience",
                    "question": "Who is the audience?",
                    "reason": "It changes the depth.",
                    "input_type": "text",
                }
            ],
        }
    )
    clarification = InitialPlanDecision.model_validate(
        {
            "kind": "clarification_required",
            "plan": _draft().model_dump(mode="json"),
            "reason": "The audience is missing.",
            "questions": [
                {
                    "field": "audience",
                    "question": "Who is the audience?",
                    "reason": "It changes the depth.",
                    "input_type": "text",
                }
            ],
        }
    )

    assert ready.reason is None
    assert ready.questions == ()
    assert clarification.plan is None


def test_planning_draft_rejects_cross_object_mismatch() -> None:
    outline = _outline(8)
    mismatched_outline = outline.model_copy(
        update={
            "pages": (
                OutlinePage(page_index=1, objective="different"),
                *outline.pages[1:],
            )
        }
    )

    with pytest.raises(ValidationError, match="outline"):
        PlanningDraft(
            brief=PresentationBrief(topic="Roadmap", page_count=8),
            outline=mismatched_outline,
            design_system=_design_system(),
            page_specs=_pages(8, outline=outline),
        )
