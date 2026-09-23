import pytest

from ppt_agent.domain.contracts import ClarificationQuestion
from ppt_agent.tools.clarification import (
    ClarificationContext,
    ClarificationValidationError,
    build_clarification_form,
    build_plan_review_form,
    validate_form_response,
)


def _question(**overrides: object) -> ClarificationQuestion:
    values: dict[str, object] = {
        "field": "audience",
        "question": "Who is the audience?",
        "reason": "The language depends on it.",
        "input_type": "single_select",
        "options": ("engineers", "executives"),
        "recommended_value": "engineers",
    }
    values.update(overrides)
    return ClarificationQuestion(**values)


def test_build_clarification_form_is_versioned_and_validates_answers() -> None:
    form = build_clarification_form(
        questions=(_question(),),
        reason="Need audience",
        context=ClarificationContext(
            input_refs=("source-1",),
            prompt_version="brief-v1",
            output_schema_name="brief_decision",
            output_schema_version=1,
            step_generation=1,
        ),
    )

    assert form["schema_name"] == "clarification_form"
    assert form["schema_version"] == 1
    assert validate_form_response(form, {"audience": "engineers"}) == {
        "audience": "engineers"
    }
    with pytest.raises(ClarificationValidationError, match="unknown answer"):
        validate_form_response(form, {"audience": "engineers", "api_key": "leak"})
    with pytest.raises(ClarificationValidationError, match="option"):
        validate_form_response(form, {"audience": "children"})


def test_clarification_form_rejects_duplicate_fields_and_bad_recommendations() -> None:
    context = ClarificationContext((), "brief-v1", "brief_decision", 1, 1)
    with pytest.raises(ClarificationValidationError, match="duplicate"):
        build_clarification_form(
            questions=(_question(), _question()), reason="Need data", context=context
        )
    with pytest.raises(ClarificationValidationError, match="recommended"):
        build_clarification_form(
            questions=(_question(recommended_value="unknown"),),
            reason="Need data",
            context=context,
        )


def test_plan_review_requires_feedback_and_valid_page_indexes_for_revision() -> None:
    form = build_plan_review_form(page_count=3, artifact_refs=("brief-1",))

    assert validate_form_response(form, {"action": "approve"}) == {
        "action": "approve"
    }
    with pytest.raises(ClarificationValidationError, match="feedback"):
        validate_form_response(
            form, {"action": "revise", "scope": "pages", "page_indexes": [1]}
        )
    with pytest.raises(ClarificationValidationError, match="duplicate"):
        validate_form_response(
            form,
            {
                "action": "revise",
                "feedback": "Change pages",
                "scope": "pages",
                "page_indexes": [1, 1],
            },
        )
    with pytest.raises(ClarificationValidationError, match="range"):
        validate_form_response(
            form,
            {
                "action": "revise",
                "feedback": "Change pages",
                "scope": "pages",
                "page_indexes": [4],
            },
        )

