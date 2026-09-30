"""Fixed-viewport Chromium renderer for repository-owned page templates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ppt_agent.domain.production import (
    BoxV1,
    DOMLayoutReportV1,
    LayoutElementReportV1,
    RenderManifestV1,
)
from ppt_agent.rendering.templates import render_html


class ChromiumUnavailable(RuntimeError):
    pass


class ChromiumRenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenderResult:
    png: bytes
    report: DOMLayoutReportV1


class ChromiumRenderer:
    def __init__(self, *, executable_path: str | None = None, timeout_seconds: float = 30.0) -> None:
        self._executable_path = executable_path
        self._timeout = timeout_seconds

    async def render(self, manifest: RenderManifestV1, visual_urls: dict[str, str]) -> RenderResult:
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ChromiumUnavailable("Playwright is not installed") from exc
        network_requests = 0
        try:
            async with asyncio.timeout(self._timeout):
                async with async_playwright() as playwright:
                    try:
                        browser = await playwright.chromium.launch(
                            executable_path=self._executable_path,
                            headless=True,
                            args=["--disable-extensions", "--disable-background-networking", "--no-first-run"],
                        )
                    except PlaywrightError as exc:
                        raise ChromiumUnavailable(
                            "Chromium is unavailable; run `python -m playwright install chromium`"
                        ) from exc
                    try:
                        context = await browser.new_context(
                            viewport={"width": 1920, "height": 1080},
                            device_scale_factor=1,
                            java_script_enabled=True,
                            service_workers="block",
                        )
                        page = await context.new_page()

                        async def block_network(route) -> None:
                            nonlocal network_requests
                            network_requests += 1
                            await route.abort()

                        await page.route("http://**/*", block_network)
                        await page.route("https://**/*", block_network)
                        await page.set_content(render_html(manifest, visual_urls), wait_until="load")
                        await page.evaluate("document.fonts.ready")
                        elements: list[LayoutElementReportV1] = []
                        for element in (*manifest.page.visual_regions, *manifest.page.text_blocks):
                            locator = page.locator(f'[data-id="{element.id}"]')
                            bounds = await locator.bounding_box()
                            if bounds is None:
                                raise ChromiumRenderError(f"rendered element is missing: {element.id}")
                            overflowed = bool(await locator.evaluate("el => el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth"))
                            font_size = None
                            if hasattr(element, "typography"):
                                font_size = float(await locator.evaluate("el => parseFloat(getComputedStyle(el).fontSize)"))
                            elements.append(LayoutElementReportV1(
                                element_id=element.id,
                                box=BoxV1(
                                    x=max(0, round(bounds["x"])), y=max(0, round(bounds["y"])),
                                    width=max(1, round(bounds["width"])), height=max(1, round(bounds["height"])),
                                ),
                                font_size=font_size, overflowed=overflowed,
                            ))
                        png = await page.screenshot(type="png")
                        await context.close()
                    finally:
                        await browser.close()
        except TimeoutError as exc:
            raise ChromiumRenderError("Chromium rendering timed out") from exc
        report = DOMLayoutReportV1(
            page_id=manifest.page.page_id,
            elements=tuple(elements),
            network_requests=network_requests,
        )
        return RenderResult(png=png, report=report)
