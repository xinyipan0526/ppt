"""Versioned clarification and planning-review form protocol."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ppt_agent.domain.contracts import ClarificationQuestion

if TYPE_CHECKING:
    from ppt_agent.runtime.repository import InterruptSnapshot, WorkflowRepository


class ClarificationValidationError(ValueError):
    """Raised before an invalid form or response can mutate workflow state."""


@dataclass(frozen=True, slots=True)
class ClarificationContext:
    input_refs: tuple[str, ...]
    prompt_version: str
    output_schema_name: str
    output_schema_version: int
    step_generation: int


def _validate_question(question: ClarificationQuestion) -> None:
    options = question.options
    if question.input_type in {"single_select", "multi_select"}:
        if not options or any(not option.strip() for option in options):
            raise ClarificationValidationError("select options must be non-empty")
        if len(set(options)) != len(options):
            raise ClarificationValidationError("select options must be unique")
    elif options:
        raise ClarificationValidationError("non-select questions cannot define options")

    recommended = question.recommended_value
    if recommended is None:
        return
    if question.input_type == "number":
        if isinstance(recommended, bool) or not isinstance(recommended, int):
            raise ClarificationValidationError("recommended value must be a number")
    elif question.input_type in {"text", "single_select"}:
        if not isinstance(recommended, str):
            raise ClarificationValidationError("recommended value must be text")
        if question.input_type == "single_select" and recommended not in options:
            raise ClarificationValidationError("recommended value must be an option")
    else:
        if not isinstance(recommended, tuple) or not all(
            isinstance(item, str) and item in options for item in recommended
        ):
            raise ClarificationValidationError(
                "recommended values must be a tuple of options"
            )


def build_clarification_form(
    *,
    questions: tuple[ClarificationQuestion, ...],
    reason: str,
    context: ClarificationContext,
) -> dict[str, Any]:
    if not 1 <= len(questions) <= 4:
        raise ClarificationValidationError("clarification requires one to four questions")
    fields = [question.field for question in questions]
    if len(set(fields)) != len(fields):
        raise ClarificationValidationError("duplicate clarification field")
    for question in questions:
        _validate_question(question)
    return {
        "schema_name": "clarification_form",
        "schema_version": 1,
        "kind": "clarification",
        "reason": reason,
        "fields": [
            {
                "name": question.field,
                "type": question.input_type,
                "question": question.question,
                "reason": question.reason,
                "options": list(question.options),
                "recommended_value": question.recommended_value,
                "required": question.required,
            }
            for question in questions
        ],
        "context": {
            "input_refs": list(context.input_refs),
            "prompt_version": context.prompt_version,
            "output_schema_name": context.output_schema_name,
            "output_schema_version": context.output_schema_version,
            "step_generation": context.step_generation,
        },
    }


def build_plan_review_form(
    *, page_count: int, artifact_refs: tuple[str, ...]
) -> dict[str, Any]:
    if not 1 <= page_count <= 50:
        raise ClarificationValidationError("page count must be in range 1..50")
    return {
        "schema_name": "plan_review",
        "schema_version": 1,
        "kind": "plan_review",
        "page_count": page_count,
        "artifact_refs": list(artifact_refs),
    }


def _validate_scalar(field: dict[str, Any], value: Any) -> Any:
    field_type = field.get("type")
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ClarificationValidationError(f"{field['name']} must be a number")
        name = field["name"]
        if name == "page_count" and not 1 <= value <= 50:
            raise ClarificationValidationError("page_count must be in range 1..50")
        if name == "budget_minor_units" and value < 0:
            raise ClarificationValidationError("budget_minor_units cannot be negative")
    elif field_type in {"text", "single_select"}:
        if not isinstance(value, str) or not value.strip():
            raise ClarificationValidationError(f"{field['name']} must be non-empty text")
        if field_type == "single_select" and value not in field.get("options", []):
            raise ClarificationValidationError(f"{field['name']} must be a listed option")
    elif field_type == "multi_select":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ClarificationValidationError(f"{field['name']} must be a string list")
        options = field.get("options", [])
        if any(item not in options for item in value):
            raise ClarificationValidationError(f"{field['name']} contains an unknown option")
    else:
        raise ClarificationValidationError("unknown form field type")
    return value


def _validate_clarification_response(
    form_schema: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    fields = form_schema.get("fields")
    if not isinstance(fields, list):
        raise ClarificationValidationError("invalid clarification form")
    by_name = {field.get("name"): field for field in fields if isinstance(field, dict)}
    unknown = set(payload) - set(by_name)
    if unknown:
        raise ClarificationValidationError(f"unknown answer fields: {sorted(unknown)}")
    missing = {
        name
        for name, field in by_name.items()
        if field.get("required", True) and name not in payload
    }
    if missing:
        raise ClarificationValidationError(f"missing required answers: {sorted(missing)}")
    return {
        name: _validate_scalar(by_name[name], value)
        for name, value in payload.items()
    }


def _validate_review_response(
    form_schema: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    allowed = {"action", "feedback", "scope", "page_indexes"}
    unknown = set(payload) - allowed
    if unknown:
        raise ClarificationValidationError(f"unknown answer fields: {sorted(unknown)}")
    action = payload.get("action")
    if action not in {"approve", "revise", "cancel"}:
        raise ClarificationValidationError("action must be approve, revise, or cancel")
    if action != "revise":
        if set(payload) != {"action"}:
            raise ClarificationValidationError(f"{action} accepts only action")
        return {"action": action}
    feedback = payload.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ClarificationValidationError("revision feedback is required")
    scope = payload.get("scope")
    if scope not in {"brief", "outline", "design", "pages", "auto"}:
        raise ClarificationValidationError("invalid revision scope")
    indexes = payload.get("page_indexes", [])
    if not isinstance(indexes, list) or any(
        isinstance(index, bool) or not isinstance(index, int) for index in indexes
    ):
        raise ClarificationValidationError("page_indexes must be an integer list")
    if len(indexes) != len(set(indexes)):
        raise ClarificationValidationError("duplicate page indexes")
    page_count = form_schema.get("page_count")
    if any(index < 1 or index > page_count for index in indexes):
        raise ClarificationValidationError("page index outside valid range")
    if scope != "pages" and indexes:
        raise ClarificationValidationError("page indexes require pages scope")
    return {
        "action": action,
        "feedback": feedback.strip(),
        "scope": scope,
        "page_indexes": indexes,
    }


def validate_form_response(
    form_schema: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    schema_name = form_schema.get("schema_name")
    schema_version = form_schema.get("schema_version")
    if schema_version != 1:
        raise ClarificationValidationError("unsupported form schema version")
    if schema_name == "clarification_form":
        return _validate_clarification_response(form_schema, payload)
    if schema_name == "plan_review":
        return _validate_review_response(form_schema, payload)
    raise ClarificationValidationError("unsupported form schema name")


class RequestClarificationTool:
    def create(
        self,
        *,
        repository: "WorkflowRepository",
        run_id: str,
        step_id: str,
        questions: tuple[ClarificationQuestion, ...],
        reason: str,
        context: ClarificationContext,
    ) -> "InterruptSnapshot":
        form = build_clarification_form(
            questions=questions, reason=reason, context=context
        )
        return repository.create_interrupt(
            run_id=run_id, step_id=step_id, form_schema=form
        )

