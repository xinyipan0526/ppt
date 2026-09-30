import asyncio
from pathlib import Path

import pytest

from ppt_agent.domain.production import (
    BoxV1, ProductionPageSpecV1, RenderManifestV1, TextBlockV1, TypographyV1,
)
from ppt_agent.rendering.chromium import ChromiumRenderer, ChromiumUnavailable


def test_chromium_renders_fixed_page_without_network(tmp_path: Path) -> None:
    page = ProductionPageSpecV1(
        page_id="page_01", page_index=1, render_mode="deterministic",
        layout_token="title_body",
        text_blocks=(TextBlockV1(
            id="title", role="title", text="Hello",
            box=BoxV1(x=96, y=120, width=800, height=180),
            typography=TypographyV1(
                font_family_token="heading", font_size=68, min_font_size=44,
                line_height=1.15, font_weight=700,
            ), max_lines=2,
        ),),
    )
    manifest = RenderManifestV1(
        page=page, renderer_version="1", chromium_version="configured",
        font_fingerprint="sha256:fonts",
    )
    try:
        result = asyncio.run(ChromiumRenderer(timeout_seconds=20).render(manifest, {}))
    except ChromiumUnavailable as exc:
        pytest.skip(str(exc))
    assert result.png.startswith(b"\x89PNG")
    assert result.report.page_id == "page_01"
    assert result.report.network_requests == 0
    assert result.report.elements[0].element_id == "title"
