"""Deterministic Brief -> Outline -> Design -> Pages -> Review workflow."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact, EdgeInput
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.contracts import (
    BriefDecision,
    ContractModel,
    DeckOutline,
    DesignSystem,
    PageSpec,
    PageSpecs,
    PlanningBundle,
    PresentationBrief,
    SchemaEnvelope,
)
from ppt_agent.domain.state_machine import RunEvent, RunStatus, StepEvent
from ppt_agent.operations.provider import PrepareAttempt
from ppt_agent.operations.text_runner import TextOperationRunner, TextPricing
from ppt_agent.providers.text import ProviderCallError, StructuredTextRequest
from ppt_agent.tools.clarification import (
    ClarificationContext,
    RequestClarificationTool,
    build_plan_review_form,
)
from ppt_agent.workflows.planning_store import (
    PlanRevisionRequest,
    PlanningStage,
    PlanningStep,
    PlanningStore,
)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    identity: str
    version: int
    schema_name: str
    content: str


def load_prompt(identity: str, version: int) -> PromptTemplate:
    if version != 1:
        raise ValueError(f"unsupported prompt version: {identity}/v{version}")
    path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "deck_planning"
        / f"{identity}-v{version}.md"
    )
    content = path.read_text(encoding="utf-8")
    header: dict[str, str] = {}
    for line in content.splitlines()[:4]:
        if ":" in line:
            key, value = line.split(":", 1)
            header[key.strip()] = value.strip()
    if header.get("identity") != identity or int(header.get("version", 0)) != version:
        raise ValueError("prompt identity does not match requested version")
    schema_name = header.get("schema")
    if not schema_name or "<untrusted_source_content>" not in content:
        raise ValueError("prompt lacks schema identity or untrusted-data boundary")
    return PromptTemplate(identity, version, schema_name, content)


class DeckPlanningWorkflow:
    def __init__(
        self,
        *,
        store: PlanningStore,
        artifacts: ArtifactStore,
        runner: TextOperationRunner,
        account_id: str,
        pricing: TextPricing,
        model: str,
        reservation_minor_units: int,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self._runner = runner
        self._account_id = account_id
        self._pricing = pricing
        self._model = model
        self._reservation_minor_units = reservation_minor_units
        self._clarification = RequestClarificationTool()

    async def create_run(
        self,
        *,
        project_id: str,
        normalized_text: str,
        source_refs: tuple[str, ...] = (),
    ):
        return self.store.create_run(project_id, source_refs, normalized_text)

    async def execute(self, run_id: str) -> None:
        run = self.store.repository.get_run(run_id)
        if run.status in {
            RunStatus.WAITING_FOR_USER,
            RunStatus.WAITING_FOR_RETRY,
            RunStatus.RECOVERY_REQUIRED,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return
        if run.status is RunStatus.QUEUED:
            self.store.repository.transition_run(run_id, RunEvent.DISPATCHED)

        while self.store.repository.get_run(run_id).status is RunStatus.RUNNING:
            step = self.store.next_step(run_id)
            self.store.repository.transition_step(step.step_id, StepEvent.STARTED)
            if self._recover_committed_step(run_id, step):
                continue
            try:
                if step.stage == "build_brief":
                    if not await self._build_brief(run_id, step):
                        return
                elif step.stage == "build_outline":
                    await self._build_outline(run_id, step)
                elif step.stage == "build_design_system":
                    await self._build_design(run_id, step)
                elif step.stage == "build_page_specs":
                    await self._build_pages(run_id, step)
                else:
                    if not self._review(run_id, step):
                        return
            except ProviderCallError as exc:
                if exc.code == "unknown_remote_outcome":
                    self.store.repository.mark_run_recovery_required(
                        run_id, reason=exc.code
                    )
                else:
                    self.store.repository.transition_step(
                        step.step_id, StepEvent.FAILED, payload={"error_code": exc.code}
                    )
                    self.store.repository.transition_run(
                        run_id,
                        RunEvent.UNRECOVERABLE_FAILURE,
                        payload={"error_code": exc.code},
                    )
                return

    async def _build_brief(self, run_id: str, step: PlanningStep) -> bool:
        current = self.store.current_input(run_id)
        content: dict[str, Any] = {
            "normalized_text": current.normalized_text,
            "source_refs": current.source_refs,
        }
        resumed = self.store.step_resume_payload(step.step_id)
        if resumed:
            current = self.store.record_clarification(run_id, resumed)
            content["clarification_answers"] = current.clarification_answers
        decision = cast(
            BriefDecision,
            await self._call(
                run_id,
                step,
                prompt_identity="brief",
                output_type=BriefDecision,
                content=content,
            ),
        )
        if decision.kind == "clarification_required":
            self._clarification.create(
                repository=self.store.repository,
                run_id=run_id,
                step_id=step.step_id,
                questions=decision.questions,
                reason=decision.reason or "More information is required.",
                context=ClarificationContext(
                    input_refs=current.source_refs,
                    prompt_version="brief-v1",
                    output_schema_name="brief_decision",
                    output_schema_version=1,
                    step_generation=step.generation,
                ),
            )
            return False
        assert decision.brief is not None
        artifact = self._write_artifact(
            run_id,
            "planning/brief",
            "planning.brief",
            "presentation_brief",
            decision.brief,
            (),
            operation_id=self._operation_id(run_id, step),
        )
        self.store.complete_step(step.step_id, (artifact,))
        return True

    async def _build_outline(self, run_id: str, step: PlanningStep) -> None:
        brief_id = self._one_ref(run_id, "build_brief")
        brief = PresentationBrief.model_validate(self._read_payload(brief_id))

        def validate(value: ContractModel) -> None:
            outline = cast(DeckOutline, value)
            if len(outline.pages) != brief.page_count:
                raise ValueError("outline pages must match brief page count")

        outline = cast(
            DeckOutline,
            await self._call(
                run_id,
                step,
                prompt_identity="outline",
                output_type=DeckOutline,
                content={"brief": brief.model_dump(mode="json")},
                validate_output=validate,
            ),
        )
        artifact = self._write_artifact(
            run_id,
            "planning/outline",
            "planning.outline",
            "deck_outline",
            outline,
            (brief_id,),
            operation_id=self._operation_id(run_id, step),
        )
        self.store.complete_step(step.step_id, (artifact,))

    async def _build_design(self, run_id: str, step: PlanningStep) -> None:
        brief_id = self._one_ref(run_id, "build_brief")
        outline_id = self._one_ref(run_id, "build_outline")
        design = cast(
            DesignSystem,
            await self._call(
                run_id,
                step,
                prompt_identity="design-system",
                output_type=DesignSystem,
                content={
                    "brief": self._read_payload(brief_id),
                    "outline": self._read_payload(outline_id),
                },
            ),
        )
        artifact = self._write_artifact(
            run_id,
            "planning/design-system",
            "planning.design_system",
            "design_system",
            design,
            (brief_id, outline_id),
            operation_id=self._operation_id(run_id, step),
        )
        self.store.complete_step(step.step_id, (artifact,))

    async def _build_pages(self, run_id: str, step: PlanningStep) -> None:
        brief_id = self._one_ref(run_id, "build_brief")
        outline_id = self._one_ref(run_id, "build_outline")
        design_id = self._one_ref(run_id, "build_design_system")
        brief = PresentationBrief.model_validate(self._read_payload(brief_id))
        outline = DeckOutline.model_validate(self._read_payload(outline_id))

        def validate(value: ContractModel) -> None:
            candidate = cast(PageSpecs, value)
            if candidate.brief_page_count != brief.page_count or candidate.outline != outline:
                raise ValueError("page specs do not match current brief and outline")

        pages = cast(
            PageSpecs,
            await self._call(
                run_id,
                step,
                prompt_identity="page-specs",
                output_type=PageSpecs,
                content={
                    "brief": brief.model_dump(mode="json"),
                    "outline": outline.model_dump(mode="json"),
                    "design_system": self._read_payload(design_id),
                },
                validate_output=validate,
            ),
        )
        operation_id = self._operation_id(run_id, step)
        aggregate_id = self._write_artifact(
            run_id,
            "planning/page-specs-source",
            "planning.page_specs",
            "page_specs",
            pages,
            (brief_id, outline_id, design_id),
            operation_id=operation_id,
        )
        artifact_ids = tuple(
            self._write_artifact(
                run_id,
                f"planning/pages/{page.page_index}",
                "planning.page_spec",
                "page_spec",
                page,
                (aggregate_id,),
                operation_id=operation_id,
            )
            for page in pages.pages
        )
        self.store.complete_step(step.step_id, artifact_ids)

    def _review(self, run_id: str, step: PlanningStep) -> bool:
        payload = self.store.step_resume_payload(step.step_id)
        if payload is None:
            brief = PresentationBrief.model_validate(
                self._read_payload(self._one_ref(run_id, "build_brief"))
            )
            refs = self._all_planning_refs(run_id)
            self.store.repository.create_interrupt(
                run_id=run_id,
                step_id=step.step_id,
                form_schema=build_plan_review_form(
                    page_count=brief.page_count, artifact_refs=refs
                ),
            )
            return False
        action = payload["action"]
        if action == "approve":
            refs = self._all_planning_refs(run_id)
            brief_id = self._one_ref(run_id, "build_brief")
            outline_id = self._one_ref(run_id, "build_outline")
            design_id = self._one_ref(run_id, "build_design_system")
            page_ids = self.store.stage_output_refs(run_id, "build_page_specs")
            bundle = PlanningBundle(
                brief_artifact_id=brief_id,
                outline_artifact_id=outline_id,
                design_system_artifact_id=design_id,
                page_spec_artifact_ids=page_ids,
            )
            bundle_id = self._write_artifact(
                run_id,
                "planning/bundle",
                "planning.bundle",
                "planning_bundle",
                bundle,
                refs,
                edge_type=ArtifactEdgeType.COMPOSED_FROM,
            )
            self.store.complete_step(step.step_id, (bundle_id,))
            self.store.finish_run(run_id, (bundle_id, *refs))
            return False
        if action == "cancel":
            self.store.repository.transition_step(step.step_id, StepEvent.CANCEL_REQUESTED)
            self.store.repository.transition_run(run_id, RunEvent.CANCEL_REQUESTED)
            return False

        brief = PresentationBrief.model_validate(
            self._read_payload(self._one_ref(run_id, "build_brief"))
        )
        request = PlanRevisionRequest.model_validate(payload)
        if any(index > brief.page_count for index in request.page_indexes):
            raise ValueError("page index outside current brief range")
        roots = self._revision_roots(run_id, request)
        self.store.complete_step(step.step_id, ())
        for artifact_id in roots:
            self.artifacts.mark_stale(artifact_id)
        self.store.apply_revision(run_id, request)
        return True

    async def _call(
        self,
        run_id: str,
        step: PlanningStep,
        *,
        prompt_identity: str,
        output_type: type[ContractModel],
        content: dict[str, Any],
        validate_output=None,
    ) -> ContractModel:
        prompt = load_prompt(prompt_identity, 1)
        run = self.store.repository.get_run(run_id)
        request = StructuredTextRequest(
            model=self._model,
            schema_name=prompt.schema_name,
            schema_version=1,
            json_schema=output_type.model_json_schema(),
            system_prompt=prompt.content,
            user_content=json.dumps(content, ensure_ascii=False, sort_keys=True),
            max_output_tokens=8_000,
            user_id=run.project_id,
        )
        operation = PrepareAttempt(
            operation_id=self._operation_id(run_id, step),
            operation_kind="text.structured",
            request_fingerprint="computed-by-text-runner",
            provider="deepseek",
            model=self._model,
            account_id=self._account_id,
            reservation_amount=self._reservation_minor_units,
            currency=self._pricing.currency,
            pricing_version=self._pricing.version,
            idempotency_key=f"{run_id}:{step.stage}:{step.generation}",
            run_id=run_id,
            step_id=step.step_id,
        )
        result = await self._runner.execute(
            request=request,
            output_type=output_type,
            operation=operation,
            pricing=self._pricing,
            validate_output=validate_output,
        )
        return result.output

    def _write_artifact(
        self,
        run_id: str,
        logical_id: str,
        kind: str,
        schema_name: str,
        value: ContractModel,
        dependencies: tuple[str, ...],
        *,
        edge_type: ArtifactEdgeType = ArtifactEdgeType.DERIVED_FROM,
        operation_id: str | None = None,
    ) -> str:
        run = self.store.repository.get_run(run_id)
        body = json.dumps(
            SchemaEnvelope(
                schema_name=schema_name, schema_version=1, payload=value
            ).model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = f"sha256:{sha256(body).hexdigest()}"
        staging = self.artifacts.create_staging(
            CreateArtifact(
                project_id=run.project_id,
                logical_id=logical_id,
                kind=kind,
                media_type="application/json",
                extension=".json",
                spec_fingerprint=fingerprint,
                metadata={"schema_name": schema_name, "schema_version": 1, "run_id": run_id},
                edges=tuple(EdgeInput(item, edge_type) for item in dependencies),
                operation_id=operation_id,
            )
        )
        self.artifacts.write_staging(staging.id, (body,), expected_hash=fingerprint)
        self.artifacts.commit_file(staging.id)
        self.artifacts.commit_metadata(staging.id)
        self.artifacts.make_available(staging.id)
        return staging.id

    def _recover_committed_step(self, run_id: str, step: PlanningStep) -> bool:
        if step.stage == "review_plan":
            return False
        operation_id = self._operation_id(run_id, step)
        outputs = self.artifacts.list_by_operation(operation_id)
        if not outputs:
            return False
        expected_kind = {
            "build_brief": "planning.brief",
            "build_outline": "planning.outline",
            "build_design_system": "planning.design_system",
        }.get(step.stage)
        if expected_kind is not None:
            matches = [item.id for item in outputs if item.kind == expected_kind]
            if len(matches) != 1:
                return False
            self.store.complete_step(step.step_id, (matches[0],))
            return True

        aggregate = next(
            (item for item in outputs if item.kind == "planning.page_specs"), None
        )
        if aggregate is None:
            return False
        pages = PageSpecs.model_validate(self._read_payload(aggregate.id))
        existing = {
            item.logical_id: item.id
            for item in outputs
            if item.kind == "planning.page_spec"
        }
        page_ids: list[str] = []
        for page in pages.pages:
            logical_id = f"planning/pages/{page.page_index}"
            artifact_id = existing.get(logical_id)
            if artifact_id is None:
                artifact_id = self._write_artifact(
                    run_id,
                    logical_id,
                    "planning.page_spec",
                    "page_spec",
                    page,
                    (aggregate.id,),
                    operation_id=operation_id,
                )
            page_ids.append(artifact_id)
        self.store.complete_step(step.step_id, tuple(page_ids))
        return True

    def _read_payload(self, artifact_id: str) -> dict[str, Any]:
        value = json.loads(self.artifacts.read_bytes(artifact_id))
        return cast(dict[str, Any], value["payload"])

    def _one_ref(self, run_id: str, stage: PlanningStage) -> str:
        refs = self.store.stage_output_refs(run_id, stage)
        if len(refs) != 1:
            raise RuntimeError(f"{stage} must have exactly one output")
        return refs[0]

    def _all_planning_refs(self, run_id: str) -> tuple[str, ...]:
        return (
            self._one_ref(run_id, "build_brief"),
            self._one_ref(run_id, "build_outline"),
            self._one_ref(run_id, "build_design_system"),
            *self.store.stage_output_refs(run_id, "build_page_specs"),
        )

    def _revision_roots(
        self, run_id: str, request: PlanRevisionRequest
    ) -> tuple[str, ...]:
        if request.scope == "brief":
            return (self._one_ref(run_id, "build_brief"),)
        if request.scope in {"outline", "auto"}:
            return (self._one_ref(run_id, "build_outline"),)
        if request.scope == "design":
            return (self._one_ref(run_id, "build_design_system"),)
        pages = self.store.stage_output_refs(run_id, "build_page_specs")
        indexes = request.page_indexes or tuple(range(1, len(pages) + 1))
        return tuple(pages[index - 1] for index in indexes)

    @staticmethod
    def _operation_id(run_id: str, step: PlanningStep) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"deck-planning:{run_id}:{step.stage}:{step.generation}",
            )
        )
