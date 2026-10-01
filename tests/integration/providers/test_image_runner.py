import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.ledger.costs import CostLedger
from ppt_agent.operations.image_runner import ImageOperationRunner, ImagePricing
from ppt_agent.operations.provider import PrepareAttempt, ProviderOperationService
from ppt_agent.providers.deterministic import DeterministicImageProvider
from ppt_agent.providers.image import ImageGenerationRequest, ImageGenerationResult, ImageUsage
from ppt_agent.storage.database import DatabaseConfig, create_session_factory, create_sqlite_engine, session_scope
from ppt_agent.storage.models import Base, ProjectRecord


def test_image_runner_persists_and_replays_binary_result_without_provider_call() -> None:
    path = Path(".cache/test-image-runner") / f"{uuid4()}.db"
    engine = create_sqlite_engine(DatabaseConfig(path=path))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Image"))
    account = CostLedger(sessions).create_account(project_id, "visual", "CNY", 1000)
    operations = ProviderOperationService(sessions)
    request = ImageGenerationRequest("det-image", "blue", 320, 180, "user")
    operation = PrepareAttempt(
        "image-op", "image.generate", "placeholder", "deterministic", "det-image",
        account.id, 10, "CNY", "det-v1", "key",
    )

    class CountingProvider(DeterministicImageProvider):
        calls = 0

        async def generate(self, request):
            self.calls += 1
            return await super().generate(request)

    first_provider = CountingProvider()
    second_provider = CountingProvider()
    try:
        first = asyncio.run(ImageOperationRunner(operations, first_provider, "deterministic").execute(
            request=request, operation=operation,
            pricing=ImagePricing("det-v1", "CNY", 10),
        ))
        second = asyncio.run(ImageOperationRunner(operations, second_provider, "deterministic").execute(
            request=request, operation=operation,
            pricing=ImagePricing("det-v1", "CNY", 10),
        ))
    finally:
        engine.dispose()

    assert first.content == second.content
    assert first_provider.calls == 1
    assert second_provider.calls == 0


def test_url_result_is_settled_before_download_and_replayed_without_resubmission() -> None:
    path = Path(".cache/test-image-runner") / f"{uuid4()}.db"
    engine = create_sqlite_engine(DatabaseConfig(path=path))
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project_id = str(uuid4())
    with session_scope(sessions) as session:
        session.add(ProjectRecord(id=project_id, name="Image URL"))
    ledger = CostLedger(sessions)
    account = ledger.create_account(project_id, "visual", "CNY", 1000)
    operations = ProviderOperationService(sessions)
    request = ImageGenerationRequest("seedream", "blue", 320, 180, "user")
    operation = PrepareAttempt("url-op", "image.generate", "placeholder", "seedream", "seedream", account.id, 10, "CNY", "v1", "key")

    class Provider:
        calls = 0
        async def generate(self, request):
            self.calls += 1
            return ImageGenerationResult("remote-1", "image/png", 320, 180, ImageUsage(1), {}, result_url="https://images.example/result")

    class FailingDownloader:
        async def download(self, url):
            raise RuntimeError("temporary download failure")

    class WorkingDownloader:
        async def download(self, url):
            return b"png-bytes", "image/png", 320, 180

    provider = Provider()
    try:
        with pytest.raises(RuntimeError, match="download failure"):
            asyncio.run(ImageOperationRunner(operations, provider, "seedream", downloader=FailingDownloader()).execute(
                request=request, operation=operation, pricing=ImagePricing("v1", "CNY", 10),
            ))
        assert operations.get_binary_result("url-op").result_kind == "url"
        result = asyncio.run(ImageOperationRunner(operations, provider, "seedream", downloader=WorkingDownloader()).execute(
            request=request, operation=operation, pricing=ImagePricing("v1", "CNY", 10),
        ))
    finally:
        engine.dispose()
    assert result.content == b"png-bytes"
    assert provider.calls == 1
    assert ledger.get_account(account.id).settled == 10
