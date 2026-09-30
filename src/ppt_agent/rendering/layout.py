"""Versioned deterministic compilation from planning pages to production pages."""

from __future__ import annotations

from ppt_agent.domain.contracts import PageSpec
from ppt_agent.domain.production import (
    BoxV1,
    ProductionPageSpecV1,
    TextBlockV1,
    TypographyV1,
    VisualRegionV1,
)


class LayoutOverflow(ValueError):
    pass


def compile_page(source: PageSpec) -> ProductionPageSpecV1:
    body = "\n".join(source.body_blocks)
    if len(body) > 20_000 or (source.title and len(source.title) > 200):
        raise LayoutOverflow("delivery text exceeds deterministic layout capacity")
    blocks: list[TextBlockV1] = []
    if source.title:
        blocks.append(TextBlockV1(
            id="title", role="title", text=source.title,
            box=BoxV1(x=96, y=112, width=820, height=190),
            typography=TypographyV1(
                font_family_token="heading", font_size=68,
                min_font_size=44, line_height=1.15, font_weight=700,
            ), max_lines=2,
        ))
    if body:
        blocks.append(TextBlockV1(
            id="body", role="body", text=body,
            box=BoxV1(x=96, y=340, width=820, height=590),
            typography=TypographyV1(
                font_family_token="body", font_size=34,
                min_font_size=24, line_height=1.4, font_weight=400,
            ), max_lines=12,
        ))
    visuals: tuple[VisualRegionV1, ...] = ()
    mode = "deterministic"
    if source.visual_requirements:
        mode = "hybrid"
        visuals = (VisualRegionV1(
            id="hero_visual", box=BoxV1(x=1010, y=64, width=838, height=960),
            kind="generated_image", prompt="; ".join(source.visual_requirements),
        ),)
    token = "cover" if source.page_type == "cover" else "title_body"
    return ProductionPageSpecV1(
        page_id=f"page_{source.page_index:02d}", page_index=source.page_index,
        render_mode=mode, layout_token=token,
        text_blocks=tuple(blocks), visual_regions=visuals,
        source_artifact_ids=source.source_refs,
    )

