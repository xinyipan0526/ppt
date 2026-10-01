import asyncio

import httpx
import pytest

from ppt_agent.providers.result_download import ResultDownloadError, ResultDownloader


def test_result_downloader_rejects_private_resolution_before_request() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"ignored")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            downloader = ResultDownloader(
                allowed_hosts={"results.example"},
                resolver=lambda _: ("127.0.0.1",),
                client=client,
            )
            with pytest.raises(ResultDownloadError, match="public"):
                await downloader.download("https://results.example/image.png")

    asyncio.run(scenario())
    assert calls == 0


def test_result_downloader_rejects_redirect_without_following() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            downloader = ResultDownloader(
                allowed_hosts={"results.example"},
                resolver=lambda _: ("93.184.216.34",),
                client=client,
            )
            with pytest.raises(ResultDownloadError, match="redirect"):
                await downloader.download("https://results.example/image.png")

    asyncio.run(scenario())
    assert calls == 1


def test_result_downloader_pins_connection_to_validated_address() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host or "", request.headers["host"], request.extensions["sni_hostname"]))
        # Validation happens after the request; any image bytes are sufficient
        # to prove the transport destination used the validated address.
        return httpx.Response(200, content=b"not-an-image")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            downloader = ResultDownloader(
                allowed_hosts={"results.example"},
                resolver=lambda _: ("93.184.216.34",),
                client=client,
            )
            with pytest.raises(ResultDownloadError, match="valid image"):
                await downloader.download("https://results.example/image.png?token=one")

    asyncio.run(scenario())
    assert seen == [("93.184.216.34", "results.example", "results.example")]


def test_result_downloader_rejects_oversized_body() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-type": "image/png"}, content=b"x" * 11)
            )
        ) as client:
            downloader = ResultDownloader(
                allowed_hosts={"results.example"},
                resolver=lambda _: ("93.184.216.34",),
                client=client,
                max_bytes=10,
            )
            with pytest.raises(ResultDownloadError, match="size"):
                await downloader.download("https://results.example/image.png")

    asyncio.run(scenario())
