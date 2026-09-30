import asyncio
from pathlib import Path
from uuid import uuid4

from ppt_agent.domain.vision import VisionAnalysisV1
from ppt_agent.ledger.costs import CostLedger
from ppt_agent.operations.provider import PrepareAttempt, ProviderOperationService
from ppt_agent.operations.vision_runner import OCROperationRunner, VisionOperationRunner, VisionPricing
from ppt_agent.providers.deterministic import DeterministicOCRProvider, DeterministicVisionProvider
from ppt_agent.providers.vision import OCRRequest, VisionRequest
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, ProjectRecord


def test_vision_runner_persists_validated_result_before_replay() -> None:
    path = Path(".cache/test-vision-runner") / f"{uuid4()}.db"
    engine = create_sqlite_engine(DatabaseConfig(path=path))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Vision"))
    account = CostLedger(sessions).create_account(project_id, "vision", "CNY", 1000)
    operations = ProviderOperationService(sessions)
    request = VisionRequest(
        "det-vision", b"image", "image/png", "analyze", "vision_analysis", 1,
        VisionAnalysisV1.model_json_schema(), 100, "user",
    )
    operation = PrepareAttempt(
        "vision-op", "vision.analyze", "placeholder", "deterministic", "det-vision",
        account.id, 100, "CNY", "det-v1", "key",
    )

    class CountingProvider(DeterministicVisionProvider):
        calls = 0

        async def analyze(self, request):
            self.calls += 1
            return await super().analyze(request)

    first_provider = CountingProvider()
    second_provider = CountingProvider()
    try:
        first = asyncio.run(VisionOperationRunner(operations, first_provider, "deterministic").execute(
            request=request, output_type=VisionAnalysisV1, operation=operation,
            pricing=VisionPricing("det-v1", "CNY", 1000, 1000),
        ))
        second = asyncio.run(VisionOperationRunner(operations, second_provider, "deterministic").execute(
            request=request, output_type=VisionAnalysisV1, operation=operation,
            pricing=VisionPricing("det-v1", "CNY", 1000, 1000),
        ))
    finally:
        engine.dispose()

    assert first.output == second.output
    assert first_provider.calls == 1
    assert second_provider.calls == 0


def test_ocr_runner_persists_and_replays_report() -> None:
    path = Path(".cache/test-ocr-runner") / f"{uuid4()}.db"
    engine = create_sqlite_engine(DatabaseConfig(path=path))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="OCR"))
    account = CostLedger(sessions).create_account(project_id, "ocr", "CNY", 1000)
    operations = ProviderOperationService(sessions)
    request = OCRRequest("det-ocr", b"image", "image/png", 100, "user", "Hello")
    operation = PrepareAttempt(
        "ocr-op", "vision.ocr", "placeholder", "deterministic", "det-ocr",
        account.id, 100, "CNY", "det-v1", "key",
    )
    try:
        first = asyncio.run(OCROperationRunner(operations, DeterministicOCRProvider(), "deterministic").execute(
            request=request, operation=operation,
            pricing=VisionPricing("det-v1", "CNY", 1000, 1000),
        ))
        second = asyncio.run(OCROperationRunner(operations, DeterministicOCRProvider(), "deterministic").execute(
            request=request, operation=operation,
            pricing=VisionPricing("det-v1", "CNY", 1000, 1000),
        ))
    finally:
        engine.dispose()
    assert first.report == second.report
    assert second.report.full_text == "Hello"
