from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
import pytest

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.artifacts import ArtifactStatus
from ppt_agent.domain.state_machine import PageStatus, RunStatus
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import ArtifactRecord, Base, ExportRecord, PageGenerationRecord, PageRecord, ProjectRecord, QualityReportRecord, WorkflowRunRecord
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.exporting import ExportWorkflow


def test_export_workflow_is_cached_and_commits_pptx(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(artifacts, project_id, "page/1", "page_image", "image/png", ".png", image.getvalue(), "page-1")
    quality_artifact = commit_bytes(artifacts, project_id, "page/1/quality", "quality_report", "application/json", ".json", b'{"passed":true}', "quality-1")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page_artifact.id, page_spec_version_id=page_artifact.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page_artifact.id, page_image_artifact_id=page_artifact.id, quality_artifact_id=quality_artifact.id))
        session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality_artifact.id, passed=True, issue_summary={"issues": []}))
    workflow = ExportWorkflow(sessions, artifacts)
    try:
        first = workflow.create(project_id, run_id, "pptx")
        second = workflow.create(
            project_id, run_id, "pptx", jpeg_quality=92, pdf_dpi=144,
        )
        with session_scope(sessions) as session:
            record = session.get(ExportRecord, first)
            legacy_manifest = dict(record.manifest)
            legacy_manifest.pop("jpeg_quality", None)
            legacy_manifest.pop("pdf_dpi", None)
            legacy_fingerprint = "sha256:" + sha256(json.dumps(
                legacy_manifest, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            record.manifest = legacy_manifest
            record_fingerprint = record.manifest_fingerprint
        artifact_id = workflow.export(first)
        status = workflow.status(first)
        with session_scope(sessions) as session:
            export_artifact = session.get(ArtifactRecord, artifact_id)
            artifact_fingerprint = export_artifact.spec_fingerprint
    finally:
        engine.dispose()
    assert first == second
    assert record_fingerprint == legacy_fingerprint
    assert artifact_fingerprint == "export:" + sha256(
        json.dumps(legacy_manifest, separators=(",", ":")).encode()
    ).hexdigest()
    assert status.status == "available"
    assert status.artifact_id == artifact_id
    assert len(Presentation(BytesIO(artifacts.read_bytes(artifact_id))).slides) == 1


def test_relevant_non_default_quality_changes_export_identity(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(artifacts, project_id, "page/1", "page_image", "image/png", ".png", image.getvalue(), "page-1")
    quality_artifact = commit_bytes(artifacts, project_id, "page/1/quality", "quality_report", "application/json", ".json", b"{}", "quality-1")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page_artifact.id, page_spec_version_id=page_artifact.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page_artifact.id, page_image_artifact_id=page_artifact.id, quality_artifact_id=quality_artifact.id))
        session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality_artifact.id, passed=True, issue_summary={}))
    workflow = ExportWorkflow(sessions, artifacts)
    try:
        default_jpg = workflow.create(project_id, run_id, "jpg", jpeg_quality=92)
        explicit_default_jpg = workflow.create(project_id, run_id, "jpg", jpeg_quality=92, pdf_dpi=144)
        lower_quality_jpg = workflow.create(project_id, run_id, "jpg", jpeg_quality=80)
        default_pdf = workflow.create(project_id, run_id, "pdf", pdf_dpi=144)
        lower_dpi_pdf = workflow.create(project_id, run_id, "pdf", pdf_dpi=96)
        default_jpg_artifact = workflow.export(default_jpg)
        lower_quality_artifact = workflow.export(lower_quality_jpg)
        default_pdf_artifact = workflow.export(default_pdf)
        lower_dpi_artifact = workflow.export(lower_dpi_pdf)
        with session_scope(sessions) as session:
            jpg_manifest = session.get(ExportRecord, lower_quality_jpg).manifest
            pdf_manifest = session.get(ExportRecord, lower_dpi_pdf).manifest
    finally:
        engine.dispose()

    assert default_jpg == explicit_default_jpg
    assert lower_quality_jpg != default_jpg
    assert lower_dpi_pdf != default_pdf
    assert jpg_manifest["jpeg_quality"] == 80
    assert pdf_manifest["pdf_dpi"] == 96
    assert artifacts.read_bytes(default_jpg_artifact) != artifacts.read_bytes(lower_quality_artifact)
    default_pdf_page = PdfReader(BytesIO(artifacts.read_bytes(default_pdf_artifact))).pages[0]
    lower_dpi_page = PdfReader(BytesIO(artifacts.read_bytes(lower_dpi_artifact))).pages[0]
    assert float(default_pdf_page.mediabox.width) == 960
    assert float(lower_dpi_page.mediabox.width) == 1440


def test_export_create_is_concurrency_safe_and_replay_reuses_artifact(tmp_path: Path) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(artifacts, project_id, "page/1", "page_image", "image/png", ".png", image.getvalue(), "page-1")
    quality_artifact = commit_bytes(artifacts, project_id, "page/1/quality", "quality_report", "application/json", ".json", b'{}', "quality-1")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page_artifact.id, page_spec_version_id=page_artifact.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page_artifact.id, page_image_artifact_id=page_artifact.id, quality_artifact_id=quality_artifact.id))
        session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality_artifact.id, passed=True, issue_summary={}))
    workflow = ExportWorkflow(sessions, artifacts)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = tuple(pool.map(lambda _: workflow.create(project_id, run_id, "pptx"), range(2)))
        first_artifact = workflow.export(ids[0])
        with session_scope(sessions) as session:
            record = session.get(ExportRecord, ids[0])
            record.status = "running"
            record.artifact_id = None
        replay_artifact = workflow.export(ids[0])
        replacement = commit_bytes(
            artifacts, project_id, "page/1", "page_image", "image/png", ".png",
            image.getvalue() + b"replacement", "page-replacement",
        )
        changed_export = workflow.create(project_id, run_id, "png")
        render = workflow._render

        def change_page_during_render(output_format, pages):
            result = render(output_format, pages)
            with session_scope(sessions) as session:
                session.get(PageRecord, page_id).current_artifact_id = replacement.id
            return result

        workflow._render = change_page_during_render
        with pytest.raises(ValueError, match="pages changed"):
            workflow.export(changed_export)
        changed_status = workflow.status(changed_export)
        with session_scope(sessions) as session:
            export_count = len(list(session.query(ExportRecord)))
            artifact_count = len(list(session.query(ArtifactRecord).filter(ArtifactRecord.kind == "export_pptx")))
    finally:
        engine.dispose()
    assert ids[0] == ids[1]
    assert replay_artifact == first_artifact
    assert export_count == 2  # one deduplicated PPTX plus the page-change PNG
    assert artifact_count == 1
    assert changed_status.status == "failed"
    assert changed_status.error == {"code": "pages_changed"}
    assert changed_status.artifact_id is None


def test_terminal_export_can_be_explicitly_requeued_with_same_id_and_attempt_history(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Retry export"))
    page_artifact = commit_bytes(artifacts, project_id, "page", "page_image", "image/png", ".png", image.getvalue(), "page")
    quality_artifact = commit_bytes(artifacts, project_id, "quality", "quality_report", "application/json", ".json", b"{}", "quality")
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(id=run_id, project_id=project_id, workflow_name="deck_production", workflow_version=1, status=RunStatus.SUCCEEDED))
        session.flush()
        session.add(PageRecord(id=page_id, run_id=run_id, page_index=1, status=PageStatus.SUCCEEDED, page_stage="validation", current_artifact_id=page_artifact.id, page_spec_version_id=page_artifact.id))
        session.flush()
        session.add(PageGenerationRecord(id=generation_id, page_id=page_id, generation=1, status="succeeded", production_spec_artifact_id=page_artifact.id, page_image_artifact_id=page_artifact.id, quality_artifact_id=quality_artifact.id))
        session.add(QualityReportRecord(id=str(uuid4()), page_generation_id=generation_id, artifact_id=quality_artifact.id, passed=True, issue_summary={}))
    workflow = ExportWorkflow(sessions, artifacts)
    try:
        export_id = workflow.create(project_id, run_id, "png")
        with session_scope(sessions) as session:
            record = session.get(ExportRecord, export_id)
            record.status = "failed"
            record.error = {"code": "export_failed"}
            record.finished_at = datetime.now(UTC)
            record.attempt_count = 3

        assert workflow.create(project_id, run_id, "png") == export_id
        queued = workflow.status(export_id)
        assert queued.status == "queued"
        assert queued.error is None
        assert queued.finished_at is None
        assert queued.attempt_count == 3
        workflow.export(export_id)
        assert workflow.status(export_id).status == "available"
        assert workflow.status(export_id).attempt_count == 4

        with session_scope(sessions) as session:
            record = session.get(ExportRecord, export_id)
            record.status = "cancelled"
            record.error = {"code": "cancelled"}
            record.artifact_id = None
            record.finished_at = datetime.now(UTC)
        assert workflow.create(project_id, run_id, "png") == export_id
        assert workflow.status(export_id).status == "queued"
        workflow.fail(
            export_id,
            code="late_old_owner_failure",
            expected_attempt_count=4,
        )
        assert workflow.status(export_id).status == "queued"
    finally:
        engine.dispose()


def test_export_rejects_manifest_artifact_that_becomes_stale_during_render(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(
        artifacts, project_id, "page/1", "page_image", "image/png", ".png",
        image.getvalue(), "page-1",
    )
    quality_artifact = commit_bytes(
        artifacts, project_id, "page/1/quality", "quality_report",
        "application/json", ".json", b"{}", "quality-1",
    )
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(
            id=run_id, project_id=project_id, workflow_name="deck_production",
            workflow_version=1, status=RunStatus.SUCCEEDED,
        ))
        session.flush()
        session.add(PageRecord(
            id=page_id, run_id=run_id, page_index=1,
            status=PageStatus.SUCCEEDED, page_stage="validation",
            current_artifact_id=page_artifact.id,
            page_spec_version_id=page_artifact.id,
        ))
        session.flush()
        session.add(PageGenerationRecord(
            id=generation_id, page_id=page_id, generation=1,
            status="succeeded", production_spec_artifact_id=page_artifact.id,
            page_image_artifact_id=page_artifact.id,
            quality_artifact_id=quality_artifact.id,
        ))
        session.add(QualityReportRecord(
            id=str(uuid4()), page_generation_id=generation_id,
            artifact_id=quality_artifact.id, passed=True, issue_summary={},
        ))
    workflow = ExportWorkflow(sessions, artifacts)
    export_id = workflow.create(project_id, run_id, "png")
    render = workflow._render

    def stale_during_render(output_format, pages):
        result = render(output_format, pages)
        with session_scope(sessions) as session:
            session.get(ArtifactRecord, page_artifact.id).status = ArtifactStatus.STALE
        return result

    workflow._render = stale_during_render
    try:
        with pytest.raises(ValueError, match="pages changed"):
            workflow.export(export_id)
        snapshot = workflow.status(export_id)
    finally:
        engine.dispose()

    assert snapshot.status == "failed"
    assert snapshot.error == {"code": "pages_changed"}
    assert snapshot.artifact_id is None


def test_export_final_publish_does_not_overwrite_concurrent_terminal_state(
    tmp_path: Path, monkeypatch,
) -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=tmp_path / "state.db"))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    artifacts = ArtifactStore(sessions, tmp_path)
    project_id, run_id, page_id, generation_id = (str(uuid4()) for _ in range(4))
    image = BytesIO()
    Image.new("RGB", (1920, 1080), "navy").save(image, "PNG")
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Deck"))
    page_artifact = commit_bytes(
        artifacts, project_id, "page/1", "page_image", "image/png", ".png",
        image.getvalue(), "page-1",
    )
    quality_artifact = commit_bytes(
        artifacts, project_id, "page/1/quality", "quality_report",
        "application/json", ".json", b"{}", "quality-1",
    )
    with session_scope(sessions) as session:
        session.add(WorkflowRunRecord(
            id=run_id, project_id=project_id, workflow_name="deck_production",
            workflow_version=1, status=RunStatus.SUCCEEDED,
        ))
        session.flush()
        session.add(PageRecord(
            id=page_id, run_id=run_id, page_index=1,
            status=PageStatus.SUCCEEDED, page_stage="validation",
            current_artifact_id=page_artifact.id,
            page_spec_version_id=page_artifact.id,
        ))
        session.flush()
        session.add(PageGenerationRecord(
            id=generation_id, page_id=page_id, generation=1,
            status="succeeded", production_spec_artifact_id=page_artifact.id,
            page_image_artifact_id=page_artifact.id,
            quality_artifact_id=quality_artifact.id,
        ))
        session.add(QualityReportRecord(
            id=str(uuid4()), page_generation_id=generation_id,
            artifact_id=quality_artifact.id, passed=True, issue_summary={},
        ))
    workflow = ExportWorkflow(sessions, artifacts)
    export_id = workflow.create(project_id, run_id, "png")
    real_commit = commit_bytes

    def commit_then_cancel(*args, **kwargs):
        artifact = real_commit(*args, **kwargs)
        with session_scope(sessions) as session:
            record = session.get(ExportRecord, export_id)
            record.status = "cancelled"
            record.error = {"code": "cancelled"}
            record.artifact_id = None
        return artifact

    monkeypatch.setattr(
        "ppt_agent.workflows.exporting.commit_bytes", commit_then_cancel,
    )
    try:
        with pytest.raises(RuntimeError, match="terminal"):
            workflow.export(export_id)
        snapshot = workflow.status(export_id)
    finally:
        engine.dispose()

    assert snapshot.status == "cancelled"
    assert snapshot.error == {"code": "cancelled"}
    assert snapshot.artifact_id is None
