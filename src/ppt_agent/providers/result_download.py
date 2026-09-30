"""Bounded retrieval of ephemeral Provider image results."""

from __future__ import annotations

import asyncio
import ipaddress
from io import BytesIO
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError


class ResultDownloadError(RuntimeError):
    """A Provider result could not be downloaded inside the safety policy."""


def _resolve_public(host: str) -> tuple[str, ...]:
    values = {
        item[4][0]
        for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    }
    return tuple(sorted(values))


class ResultDownloader:
    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        resolver: Callable[[str], tuple[str, ...]] = _resolve_public,
        client: httpx.AsyncClient | None = None,
        max_bytes: int = 32 * 1024 * 1024,
        max_pixels: int = 36_000_000,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not allowed_hosts or max_bytes <= 0 or max_pixels <= 0:
            raise ValueError("download allowlist and positive limits are required")
        self._allowed_hosts = {host.casefold().rstrip(".") for host in allowed_hosts}
        self._resolver = resolver
        self._client = client
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._timeout = httpx.Timeout(timeout_seconds)

    async def download(self, url: str) -> tuple[bytes, str, int, int]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or not host or host not in self._allowed_hosts:
            raise ResultDownloadError("result URL must use an allowed HTTPS host")
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ResultDownloadError("result URL authority is not allowed")
        addresses = await asyncio.to_thread(self._resolver, host)
        if not addresses:
            raise ResultDownloadError("result host did not resolve")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ResultDownloadError("result host resolution is invalid") from exc
            if not ip.is_global:
                raise ResultDownloadError("result host must resolve only to public addresses")
        owns = self._client is None
        client = self._client or httpx.AsyncClient(follow_redirects=False)
        try:
            try:
                async with client.stream(
                    "GET", url, timeout=self._timeout, follow_redirects=False
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ResultDownloadError("Provider result redirect is not allowed")
                    if response.status_code != 200:
                        raise ResultDownloadError(
                            f"Provider result returned HTTP {response.status_code}"
                        )
                    length = response.headers.get("content-length")
                    if length is not None and int(length) > self._max_bytes:
                        raise ResultDownloadError("Provider result exceeds size limit")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_bytes:
                            raise ResultDownloadError("Provider result exceeds size limit")
                        chunks.append(chunk)
            except httpx.RequestError as exc:
                raise ResultDownloadError("Provider result download failed") from exc
        finally:
            if owns:
                await client.aclose()
        if tuple(await asyncio.to_thread(self._resolver, host)) != addresses:
            raise ResultDownloadError("result host resolution changed during download")
        content = b"".join(chunks)
        try:
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                if width * height > self._max_pixels:
                    raise ResultDownloadError("Provider result exceeds pixel limit")
                image.verify()
                media_type = Image.MIME.get(image.format or "")
        except (UnidentifiedImageError, OSError) as exc:
            raise ResultDownloadError("Provider result is not a valid image") from exc
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ResultDownloadError("Provider result image type is unsupported")
        return content, media_type, width, height
