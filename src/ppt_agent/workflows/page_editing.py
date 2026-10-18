"""Idempotent single-page editing with immutable generation lineage."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Awaitable, Callable
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select, text

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.production import ProductionPageSpecV1
from ppt_agent.domain.vision import (
    NormalizedBoxV1, PageEditPlanV1, PageEditRequestV1, QualityReportV1,
)
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    PageEditRecord, PageGenerationRecord, PageRecord, QualityReportRecord,
    WorkflowRunRecord,
)
from ppt_agent.tools.page_editing import compile_edit_plan
from ppt_agent.workflows.artifact_pipeline import commit_bytes


@dataclass(frozen=True, slots=True)
class EditExecutionContext:
    edit_id: str
    page_id: str
    project_id: str
    source_artifact_id: str
    render_manifest_artifact_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class EditRenderResult:
    content: bytes
    render_manifest_artifact_id: str | None = None
    visual_artifact_id: str | None = None
    quality_report: QualityReportV1 | None = None


EditRenderer = Callable[
    [PageEditPlanV1, bytes, EditExecutionContext],
    Awaitable[bytes | EditRenderResult],
]
VisualLocator = Callable[[str, bytes, EditExecutionContext], Awaitable[NormalizedBoxV1]]


@dataclass(frozen=True, slots=True)
class PageEditSnapshot:
    id: str
    page_id: str
    status: str
    plan: PageEditPlanV1
    result_generation_id: str | None


@dataclass(frozen=True, slots=True)
class GenerationSnapshot:
    id: str
    generation: int
    status: str
    page_image_artifact_id: str | None
    source_generation_id: str | None


class PageEditingWorkflow:
    def __init__(
        self,
        sessions: SessionFactory,
        artifacts: ArtifactStore,
        *,
        local_editor: EditRenderer,
        visual_editor: EditRenderer,
        visual_locator: VisualLocator | None = None,
    ) -> None:
        self._sessions = sessions
        self._artifacts = artifacts
        self._local_editor = local_editor
        self._visual_editor = visual_editor
        self._visual_locator = visual_locator

    async def request_edit(
        self,
        page_id: str,
        request: PageEditRequestV1,
        idempotency_key: str,
        *,
        target_box: NormalizedBoxV1 | None = None,
    ) -> PageEditSnapshot:
        if not idempotency_key or len(idempotency_key) > 255:
            raise ValueError("a bounded idempotency key is required")
        with session_scope(self._sessions) as session:
            page = session.get(PageRecord, page_id)
            if page is None:
                raise LookupError("page not found")
            source = session.scalar(select(PageGenerationRecord).where(PageGenerationRecord.page_id == page_id).order_by(PageGenerationRecord.generation.desc()))
            if source is None or not source.page_image_artifact_id:
                raise ValueError("page has no editable generation")
            existing = session.scalar(select(PageEditRecord).where(PageEditRecord.page_id == page_id, PageEditRecord.idempotency_key == idempotency_key))
            if existing is not None:
                return self._snapshot(existing)
            proxy = self._page_spec(source, page)
            plan = compile_edit_plan(request, proxy, target_box=target_box)
            source_artifact_id = source.page_image_artifact_id
            project_id = session.get(WorkflowRunRecord, page.run_id).project_id
            edit_id = str(uuid4())
        visual_tokens = ("图片", "插图", "背景", "image", "visual", "background")
        if (
            plan.kind == "ambiguous" and target_box is None and self._visual_locator is not None
            and any(token in request.instruction.casefold() for token in visual_tokens)
        ):
            context = EditExecutionContext(
                edit_id, page_id, project_id, source_artifact_id,
                source.render_manifest_artifact_id,
                page.run_id,
            )
            located = await self._visual_locator(request.instruction, self._artifacts.read_bytes(source_artifact_id), context)
            plan = compile_edit_plan(request, proxy, target_box=located)
        with session_scope(self._sessions) as session:
            existing = session.scalar(select(PageEditRecord).where(PageEditRecord.page_id == page_id, PageEditRecord.idempotency_key == idempotency_key))
            if existing is not None:
                return self._snapshot(existing)
            edit = PageEditRecord(
                id=edit_id, page_id=page_id, source_generation_id=source.id,
                idempotency_key=idempotency_key, request_payload=request.model_dump(mode="json"),
                plan_payload=plan.model_dump(mode="json"),
                status="awaiting_clarification" if plan.kind == "ambiguous" else "queued",
            )
            session.add(edit)
            edit_id = edit.id
        if plan.kind != "ambiguous":
            await self._execute(edit_id)
        return self.get(edit_id)

    async def resume_edit(
        self,
        edit_id: str,
        *,
        instruction: str,
        target_box: NormalizedBoxV1 | None = None,
    ) -> PageEditSnapshot:
        with session_scope(self._sessions) as session:
            edit = session.get(PageEditRecord, edit_id)
            if edit is None:
                raise LookupError("page edit not found")
            if edit.status != "awaiting_clarification":
                return self._snapshot(edit)
            page = session.get(PageRecord, edit.page_id)
            source = session.get(PageGenerationRecord, edit.source_generation_id)
            proxy = self._page_spec(source, page)
            request = PageEditRequestV1(instruction=instruction)
            plan = compile_edit_plan(request, proxy, target_box=target_box)
            if plan.kind == "ambiguous":
                edit.plan_payload = plan.model_dump(mode="json")
                return self._snapshot(edit)
            edit.request_payload = request.model_dump(mode="json")
            edit.plan_payload = plan.model_dump(mode="json")
            edit.status = "queued"
        await self._execute(edit_id)
        return self.get(edit_id)

    async def request_regeneration(self, page_id: str, idempotency_key: str) -> PageEditSnapshot:
        return await self.request_edit(
            page_id, PageEditRequestV1(instruction="重新生成整页", resample_visual=True), idempotency_key,
        )

    async def _execute(self, edit_id: str) -> None:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            edit = session.get(PageEditRecord, edit_id)
            if edit is None:
                raise LookupError("page edit not found")
            if edit.status == "succeeded":
                return
            source = session.get(PageGenerationRecord, edit.source_generation_id)
            page = session.get(PageRecord, edit.page_id)
            if source is None or page is None or not source.page_image_artifact_id:
                raise ValueError("source generation is unavailable")
            plan = PageEditPlanV1.model_validate(edit.plan_payload)
            generation_number = int(session.scalar(select(func.max(PageGenerationRecord.generation)).where(PageGenerationRecord.page_id == page.id)) or 0) + 1
            generation = PageGenerationRecord(
                id=str(uuid4()), page_id=page.id, generation=generation_number,
                status="running", production_spec_artifact_id=source.production_spec_artifact_id,
                source_generation_id=source.id,
            )
            session.add(generation)
            session.flush()
            edit.result_generation_id = generation.id
            edit.status = "running"
            source_artifact_id = source.page_image_artifact_id
            project_id = session.get(WorkflowRunRecord, page.run_id).project_id
        source_bytes = self._artifacts.read_bytes(source_artifact_id)
        editor = self._visual_editor if plan.kind in {"visual_edit", "full_page_regeneration"} else self._local_editor
        context = EditExecutionContext(
            edit_id, page.id, project_id, source_artifact_id,
            source.render_manifest_artifact_id,
            page.run_id,
        )
        rendered = await editor(plan, source_bytes, context)
        result = rendered.content if isinstance(rendered, EditRenderResult) else rendered
        with Image.open(BytesIO(result)) as image:
            if image.size != (1920, 1080):
                raise ValueError("edited page must be 1920 x 1080")
            image.thumbnail((480, 270))
            thumbnail_output = BytesIO()
            image.convert("RGB").save(thumbnail_output, "JPEG", quality=85, optimize=True)
        source_artifact = self._artifacts.get(source_artifact_id)
        fingerprint = f"edit:{sha256((edit_id + sha256(result).hexdigest()).encode()).hexdigest()}"
        page_artifact = commit_bytes(
            self._artifacts, project_id, source_artifact.logical_id, "page_image", "image/png", ".png",
            result, fingerprint, source_artifact_ids=(source_artifact_id,),
            edge_type=ArtifactEdgeType.SUPERSEDES,
        )
        quality_report = (
            rendered.quality_report
            if isinstance(rendered, EditRenderResult) and rendered.quality_report
            else QualityReportV1(page_id=page.id, passed=True, issues=())
        )
        if not quality_report.passed:
            raise ValueError("edited page failed quality validation")
        quality_content = quality_report.model_dump_json().encode("utf-8")
        quality = commit_bytes(
            self._artifacts, project_id, f"{source_artifact.logical_id}/quality/{edit_id}",
            "quality_report", "application/json", ".json", quality_content,
            f"{fingerprint}:quality", source_artifact_ids=(page_artifact.id,),
            edge_type=ArtifactEdgeType.VALIDATES,
        )
        thumbnail = commit_bytes(
            self._artifacts, project_id,
            f"{source_artifact.logical_id}/thumbnail/{edit_id}",
            "page_thumbnail", "image/jpeg", ".jpg", thumbnail_output.getvalue(),
            f"{fingerprint}:thumbnail", source_artifact_ids=(page_artifact.id,),
            edge_type=ArtifactEdgeType.DERIVED_FROM,
        )
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            edit = session.get(PageEditRecord, edit_id)
            generation = session.get(PageGenerationRecord, edit.result_generation_id)
            page = session.get(PageRecord, edit.page_id)
            generation.page_image_artifact_id = page_artifact.id
            generation.quality_artifact_id = quality.id
            generation.thumbnail_artifact_id = thumbnail.id
            generation.render_manifest_artifact_id = (
                rendered.render_manifest_artifact_id
                if isinstance(rendered, EditRenderResult)
                else source.render_manifest_artifact_id
            )
            generation.visual_artifact_id = (
                rendered.visual_artifact_id
                if isinstance(rendered, EditRenderResult)
                else source.visual_artifact_id
            )
            generation.status = "succeeded"
            page.current_artifact_id = page_artifact.id
            edit.status = "succeeded"
            session.add(QualityReportRecord(
                id=str(uuid4()), page_generation_id=generation.id, artifact_id=quality.id,
                passed=quality_report.passed,
                issue_summary={
                    "issues": [item.model_dump(mode="json") for item in quality_report.issues]
                },
            ))
        self._artifacts.mark_stale(source_artifact_id)

    def get(self, edit_id: str) -> PageEditSnapshot:
        with session_scope(self._sessions) as session:
            edit = session.get(PageEditRecord, edit_id)
            if edit is None:
                raise LookupError("page edit not found")
            return self._snapshot(edit)

    def pending_for_page(self, page_id: str) -> PageEditSnapshot | None:
        with session_scope(self._sessions) as session:
            edit = session.scalar(
                select(PageEditRecord)
                .where(
                    PageEditRecord.page_id == page_id,
                    PageEditRecord.status == "awaiting_clarification",
                )
                .order_by(PageEditRecord.created_at.desc())
            )
            return None if edit is None else self._snapshot(edit)

    def history(self, page_id: str) -> tuple[GenerationSnapshot, ...]:
        with session_scope(self._sessions) as session:
            records = session.scalars(select(PageGenerationRecord).where(PageGenerationRecord.page_id == page_id).order_by(PageGenerationRecord.generation))
            return tuple(GenerationSnapshot(item.id, item.generation, item.status, item.page_image_artifact_id, item.source_generation_id) for item in records)

    def restore_generation(
        self,
        page_id: str,
        source_generation_id: str,
        *,
        expected_current_generation_id: str,
    ) -> GenerationSnapshot:
        """Restore by copying references into a new immutable page generation."""
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            page = session.get(PageRecord, page_id)
            source = session.get(PageGenerationRecord, source_generation_id)
            if page is None or source is None or source.page_id != page_id:
                raise LookupError("page generation not found")
            run = session.get(WorkflowRunRecord, page.run_id)
            if run is None:
                raise LookupError("production run not found")
            for artifact_id in (
                source.production_spec_artifact_id,
                source.visual_artifact_id,
                source.render_manifest_artifact_id,
                source.dom_report_artifact_id,
                source.page_image_artifact_id,
                source.thumbnail_artifact_id,
                source.quality_artifact_id,
            ):
                if artifact_id is None:
                    continue
                artifact = self._artifacts.get(artifact_id)
                if artifact.project_id != run.project_id:
                    raise ValueError("page generation contains a cross-project Artifact")
            current = session.scalar(
                select(PageGenerationRecord)
                .where(
                    PageGenerationRecord.page_id == page_id,
                    PageGenerationRecord.page_image_artifact_id
                    == page.current_artifact_id,
                )
                .order_by(PageGenerationRecord.generation.desc())
            )
            if current is None or current.id != expected_current_generation_id:
                raise ValueError("page generation changed; refresh and retry")
            number = int(
                session.scalar(
                    select(func.max(PageGenerationRecord.generation)).where(
                        PageGenerationRecord.page_id == page_id
                    )
                )
                or 0
            ) + 1
            restored = PageGenerationRecord(
                id=str(uuid4()), page_id=page_id, generation=number,
                status="succeeded",
                production_spec_artifact_id=source.production_spec_artifact_id,
                visual_artifact_id=source.visual_artifact_id,
                render_manifest_artifact_id=source.render_manifest_artifact_id,
                dom_report_artifact_id=source.dom_report_artifact_id,
                page_image_artifact_id=source.page_image_artifact_id,
                thumbnail_artifact_id=source.thumbnail_artifact_id,
                quality_artifact_id=source.quality_artifact_id,
                source_generation_id=source.id,
            )
            session.add(restored)
            page.current_artifact_id = source.page_image_artifact_id
            session.flush()
            return GenerationSnapshot(
                restored.id, restored.generation, restored.status,
                restored.page_image_artifact_id, restored.source_generation_id,
            )

    def _page_spec(
        self, source: PageGenerationRecord, page: PageRecord
    ) -> ProductionPageSpecV1:
        if source.render_manifest_artifact_id:
            from ppt_agent.domain.production import RenderManifestV1

            manifest = RenderManifestV1.model_validate_json(
                self._artifacts.read_bytes(source.render_manifest_artifact_id)
            )
            return manifest.page
        return ProductionPageSpecV1(
            page_id=page.id, page_index=page.page_index,
            render_mode="deterministic", layout_token="title_body",
        )

    @staticmethod
    def _snapshot(edit: PageEditRecord) -> PageEditSnapshot:
        return PageEditSnapshot(
            edit.id, edit.page_id, edit.status,
            PageEditPlanV1.model_validate(edit.plan_payload), edit.result_generation_id,
        )
