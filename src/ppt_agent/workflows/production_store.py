"""Transactional state for deck production runs and page generations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, text

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.artifacts import ArtifactStatus
from ppt_agent.domain.production import ProductionSpecV1
from ppt_agent.domain.state_machine import (
    PageEvent, PageStatus, RunEvent, RunStatus, transition_page, transition_run,
)
from ppt_agent.events.persistence import append_event_record
from ppt_agent.runtime.repository import RunSnapshot
from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ArtifactRecord, PageGenerationRecord, PageRecord, ProductionInputRecord,
    QualityReportRecord,
    WorkflowRunRecord,
)


@dataclass(frozen=True, slots=True)
class ProductionPageSnapshot:
    id: str
    page_index: int
    status: PageStatus
    page_stage: str | None
    current_artifact_id: str | None
    generation_id: str


class ProductionStore:
    def __init__(self, sessions: SessionFactory, artifacts: ArtifactStore) -> None:
        self._sessions = sessions
        self._artifacts = artifacts

    def create_run(self, project_id: str, planning_run_id: str, bundle_artifact_id: str, spec_artifact_id: str) -> str:
        spec = ProductionSpecV1.model_validate_json(self._artifacts.read_bytes(spec_artifact_id))
        if spec.project_id != project_id or spec.planning_run_id != planning_run_id or spec.planning_bundle_artifact_id != bundle_artifact_id:
            raise ValueError("production specification identity mismatch")
        run_id = str(uuid4())
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            planning = session.get(WorkflowRunRecord, planning_run_id)
            if planning is None or planning.project_id != project_id or planning.status is not RunStatus.SUCCEEDED:
                raise ValueError("planning run must be approved and succeeded")
            for artifact_id in (bundle_artifact_id, spec_artifact_id):
                artifact = session.get(ArtifactRecord, artifact_id)
                if artifact is None or artifact.project_id != project_id or artifact.status is not ArtifactStatus.AVAILABLE:
                    raise ValueError("production inputs must be available project Artifacts")
            run = WorkflowRunRecord(
                id=run_id, project_id=project_id, workflow_name="deck_production",
                workflow_version=1, status=RunStatus.QUEUED,
            )
            session.add(run)
            session.flush()
            session.add(ProductionInputRecord(
                run_id=run_id, project_id=project_id, planning_run_id=planning_run_id,
                planning_bundle_artifact_id=bundle_artifact_id,
                production_spec_artifact_id=spec_artifact_id,
            ))
            for item in spec.pages:
                page_id, generation_id = str(uuid4()), str(uuid4())
                session.add(PageRecord(
                    id=page_id, run_id=run_id, page_index=item.page_index,
                    status=PageStatus.PLANNED, page_stage=None,
                    page_spec_version_id=spec_artifact_id,
                ))
                session.flush()
                session.add(PageGenerationRecord(
                    id=generation_id, page_id=page_id, generation=1, status="queued",
                    production_spec_artifact_id=spec_artifact_id,
                ))
            append_event_record(session, run_id=run_id, event_type="run.queued", payload={"status": "queued", "workflow": "deck_production/v1"})
        return run_id

    def load_spec(self, run_id: str) -> ProductionSpecV1:
        with session_scope(self._sessions) as session:
            inputs = session.get(ProductionInputRecord, run_id)
            if inputs is None:
                raise LookupError("production input not found")
            artifact_id = inputs.production_spec_artifact_id
        return ProductionSpecV1.model_validate_json(self._artifacts.read_bytes(artifact_id))

    def get_run(self, run_id: str) -> RunSnapshot:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError("production run not found")
            return RunSnapshot(run.id, run.project_id, run.workflow_name, run.workflow_version, run.status)

    def start_run(self, run_id: str) -> None:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            if run is None:
                raise LookupError("production run not found")
            if run.status is RunStatus.QUEUED:
                run.status = transition_run(run.status, RunEvent.DISPATCHED)
                append_event_record(session, run_id=run_id, event_type="run.run_dispatched", payload={"status": run.status.value})

    def start_page(self, page_id: str, stage: str) -> None:
        with session_scope(self._sessions) as session:
            page = session.get(PageRecord, page_id)
            if page is None:
                raise LookupError("page not found")
            if page.status is PageStatus.PLANNED:
                page.status = transition_page(page.status, PageEvent.APPROVED)
            if page.status is PageStatus.QUEUED:
                page.status = transition_page(page.status, PageEvent.STARTED)
            page.page_stage = stage
            append_event_record(session, run_id=page.run_id, page_id=page.id, event_type="page.page_started", payload={"page_index": page.page_index, "stage": stage})

    def advance_page(self, page_id: str, stage: str) -> None:
        with session_scope(self._sessions) as session:
            page = session.get(PageRecord, page_id)
            if page is None or page.status is not PageStatus.RUNNING:
                raise ValueError("page is not running")
            page.page_stage = stage

    def complete_page(
        self,
        page_id: str,
        generation_id: str,
        artifact_id: str,
        refs: dict[str, str | None],
        *,
        quality_passed: bool,
        quality_issues: list[dict[str, object]],
    ) -> None:
        with session_scope(self._sessions) as session:
            page = session.get(PageRecord, page_id)
            generation = session.get(PageGenerationRecord, generation_id)
            if page is None or generation is None or generation.page_id != page.id:
                raise LookupError("page generation not found")
            page.status = transition_page(page.status, PageEvent.COMPLETED)
            page.current_artifact_id = artifact_id
            page.page_stage = "validation"
            generation.status = "succeeded"
            generation.visual_artifact_id = refs.get("visual")
            generation.render_manifest_artifact_id = refs.get("manifest")
            generation.dom_report_artifact_id = refs.get("dom")
            generation.page_image_artifact_id = artifact_id
            generation.thumbnail_artifact_id = refs.get("thumbnail")
            generation.quality_artifact_id = refs.get("quality")
            if generation.quality_artifact_id is not None:
                session.add(QualityReportRecord(
                    id=str(uuid4()), page_generation_id=generation.id,
                    artifact_id=generation.quality_artifact_id,
                    passed=quality_passed, issue_summary={"issues": quality_issues},
                ))
            append_event_record(session, run_id=page.run_id, page_id=page.id, event_type="page.page_completed", payload={"page_index": page.page_index, "artifact_id": artifact_id})

    def complete_run(self, run_id: str) -> None:
        with session_scope(self._sessions) as session:
            run = session.get(WorkflowRunRecord, run_id)
            pages = list(session.scalars(select(PageRecord).where(PageRecord.run_id == run_id).order_by(PageRecord.page_index)))
            if run is None or not pages or any(page.status is not PageStatus.SUCCEEDED for page in pages):
                raise ValueError("all pages must succeed before the run")
            if run.status is RunStatus.SUCCEEDED:
                return
            run.status = transition_run(run.status, RunEvent.ALL_REQUIRED_STEPS_SUCCEEDED)
            run.result_refs = [page.current_artifact_id for page in pages]
            append_event_record(session, run_id=run_id, event_type="run.all_required_steps_succeeded", payload={"status": run.status.value, "page_count": len(pages)})

    def current_pages(self, run_id: str) -> tuple[ProductionPageSnapshot, ...]:
        with session_scope(self._sessions) as session:
            pages = list(session.scalars(select(PageRecord).where(PageRecord.run_id == run_id).order_by(PageRecord.page_index)))
            snapshots = []
            for page in pages:
                generation = session.scalar(select(PageGenerationRecord).where(PageGenerationRecord.page_id == page.id).order_by(PageGenerationRecord.generation.desc()))
                snapshots.append(ProductionPageSnapshot(page.id, page.page_index, page.status, page.page_stage.value if page.page_stage else None, page.current_artifact_id, generation.id))
            return tuple(snapshots)

    def generation_artifacts(self, generation_id: str) -> dict[str, str | None]:
        with session_scope(self._sessions) as session:
            generation = session.get(PageGenerationRecord, generation_id)
            if generation is None:
                raise LookupError("page generation not found")
            return {
                "visual": generation.visual_artifact_id,
                "manifest": generation.render_manifest_artifact_id,
                "dom": generation.dom_report_artifact_id,
                "page_image": generation.page_image_artifact_id,
                "thumbnail": generation.thumbnail_artifact_id,
                "quality": generation.quality_artifact_id,
            }
