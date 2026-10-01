"""Immutable deck-level snapshots built from immutable page generations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, text

from ppt_agent.storage.database import SessionFactory, session_scope
from ppt_agent.storage.models import (
    ArtifactRecord,
    DeckGenerationRecord,
    PageGenerationRecord,
    PageRecord,
    ProductionInputRecord,
    WorkflowRunRecord,
)


class DeckGenerationConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeckGenerationSnapshot:
    id: str
    project_id: str
    planning_run_id: str
    production_run_id: str
    generation: int
    status: str
    source_generation_id: str | None
    instruction: str | None
    page_generation_ids: tuple[str, ...]
    created_at: datetime


class DeckGenerationService:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    def create_for_run(
        self,
        run_id: str,
        *,
        source_generation_id: str | None = None,
        instruction: str | None = None,
    ) -> DeckGenerationSnapshot:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            run = session.get(WorkflowRunRecord, run_id)
            inputs = session.get(ProductionInputRecord, run_id)
            if run is None or inputs is None or run.workflow_name != "deck_production":
                raise LookupError("production run not found")
            if instruction is not None and len(instruction) > 2_000:
                raise DeckGenerationConflict("regeneration instruction is too long")
            page_generation_ids = self._current_page_generation_ids(session, run_id)
            generation = int(
                session.scalar(
                    select(func.max(DeckGenerationRecord.generation)).where(
                        DeckGenerationRecord.planning_run_id == inputs.planning_run_id
                    )
                )
                or 0
            ) + 1
            record = DeckGenerationRecord(
                id=str(uuid4()),
                project_id=run.project_id,
                planning_run_id=inputs.planning_run_id,
                production_run_id=run.id,
                generation=generation,
                status=run.status.value,
                source_generation_id=source_generation_id,
                instruction=instruction,
                page_generation_ids=list(page_generation_ids),
            )
            session.add(record)
            session.flush()
            return self._snapshot(record)

    def get(
        self, generation_id: str, *, project_id: str | None = None
    ) -> DeckGenerationSnapshot:
        with session_scope(self._sessions) as session:
            record = session.get(DeckGenerationRecord, generation_id)
            if record is None or (project_id is not None and record.project_id != project_id):
                raise LookupError("deck generation not found")
            self._refresh_terminal_snapshot(session, record)
            return self._snapshot(record)

    def list(self, project_id: str) -> tuple[DeckGenerationSnapshot, ...]:
        with session_scope(self._sessions) as session:
            records = list(
                session.scalars(
                    select(DeckGenerationRecord)
                    .where(DeckGenerationRecord.project_id == project_id)
                    .order_by(DeckGenerationRecord.created_at, DeckGenerationRecord.generation)
                )
            )
            for record in records:
                self._refresh_terminal_snapshot(session, record)
            return tuple(self._snapshot(item) for item in records)

    def restore(
        self, generation_id: str, *, expected_generation: int
    ) -> DeckGenerationSnapshot:
        with session_scope(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            source = session.get(DeckGenerationRecord, generation_id)
            if source is None:
                raise LookupError("deck generation not found")
            self._refresh_terminal_snapshot(session, source)
            latest = int(
                session.scalar(
                    select(func.max(DeckGenerationRecord.generation)).where(
                        DeckGenerationRecord.planning_run_id == source.planning_run_id
                    )
                )
                or 0
            )
            if latest != expected_generation:
                raise DeckGenerationConflict("deck generation changed; refresh and retry")
            pages = list(
                session.scalars(
                    select(PageRecord)
                    .where(PageRecord.run_id == source.production_run_id)
                    .order_by(PageRecord.page_index)
                )
            )
            generations = [
                session.get(PageGenerationRecord, item)
                for item in source.page_generation_ids
            ]
            if len(generations) != len(pages) or any(item is None for item in generations):
                raise DeckGenerationConflict("only a complete deck generation can be restored")
            by_page = {item.page_id: item for item in generations if item is not None}
            if set(by_page) != {page.id for page in pages}:
                raise DeckGenerationConflict("deck snapshot pages do not match its production run")
            for page in pages:
                selected = by_page[page.id]
                artifact = session.get(ArtifactRecord, selected.page_image_artifact_id)
                if artifact is None or artifact.project_id != source.project_id:
                    raise DeckGenerationConflict("deck snapshot contains an invalid page asset")
                page.current_artifact_id = selected.page_image_artifact_id
            restored = DeckGenerationRecord(
                id=str(uuid4()), project_id=source.project_id,
                planning_run_id=source.planning_run_id,
                production_run_id=source.production_run_id,
                generation=latest + 1, status="succeeded",
                source_generation_id=source.id,
                instruction="restore",
                page_generation_ids=list(source.page_generation_ids),
            )
            session.add(restored)
            session.flush()
            return self._snapshot(restored)

    def _refresh_terminal_snapshot(self, session, record: DeckGenerationRecord) -> None:
        run = session.get(WorkflowRunRecord, record.production_run_id)
        if run is None:
            return
        record.status = run.status.value
        if not record.page_generation_ids and run.status.value in {
            "succeeded", "failed", "cancelled", "recovery_required"
        }:
            record.page_generation_ids = list(
                self._current_page_generation_ids(session, run.id)
            )

    @staticmethod
    def _current_page_generation_ids(session, run_id: str) -> tuple[str, ...]:
        run = session.get(WorkflowRunRecord, run_id)
        if run is None:
            raise LookupError("production run not found")
        pages = list(
            session.scalars(
                select(PageRecord)
                .where(PageRecord.run_id == run_id)
                .order_by(PageRecord.page_index)
            )
        )
        result: list[str] = []
        for page in pages:
            if page.current_artifact_id is None:
                continue
            generation = session.scalar(
                select(PageGenerationRecord)
                .join(
                    ArtifactRecord,
                    ArtifactRecord.id == PageGenerationRecord.page_image_artifact_id,
                )
                .where(
                    PageGenerationRecord.page_id == page.id,
                    PageGenerationRecord.status == "succeeded",
                    PageGenerationRecord.page_image_artifact_id.is_not(None),
                    PageGenerationRecord.page_image_artifact_id == page.current_artifact_id,
                    ArtifactRecord.project_id == run.project_id,
                )
                .order_by(PageGenerationRecord.generation.desc())
            )
            if generation is not None:
                result.append(generation.id)
        return tuple(result)

    @staticmethod
    def _snapshot(record: DeckGenerationRecord) -> DeckGenerationSnapshot:
        return DeckGenerationSnapshot(
            record.id, record.project_id, record.planning_run_id,
            record.production_run_id, record.generation, record.status,
            record.source_generation_id, record.instruction,
            tuple(record.page_generation_ids), record.created_at,
        )
