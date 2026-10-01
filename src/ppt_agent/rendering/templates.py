"""Repository-owned HTML generation; no model-authored markup is accepted."""

from __future__ import annotations

from html import escape

from ppt_agent.domain.production import RenderManifestV1


def render_html(manifest: RenderManifestV1, visual_urls: dict[str, str]) -> str:
    elements: list[str] = []
    for region in manifest.page.visual_regions:
        box = region.box
        style = f"left:{box.x}px;top:{box.y}px;width:{box.width}px;height:{box.height}px"
        url = visual_urls.get(region.id)
        if url:
            elements.append(f'<img data-id="{escape(region.id)}" src="{escape(url)}" style="{style};object-fit:cover">')
    for block in manifest.page.text_blocks:
        box, typography = block.box, block.typography
        font_family = {
            "heading": '"Noto Sans CJK SC","Microsoft YaHei",Arial,sans-serif',
            "body": '"Noto Sans CJK SC","Microsoft YaHei",Arial,sans-serif',
            "mono": '"Noto Sans Mono CJK SC",Consolas,monospace',
            "symbol": '"Noto Sans Symbols 2","Segoe UI Symbol",sans-serif',
        }[typography.font_family_token]
        style = (
            f"left:{box.x}px;top:{box.y}px;width:{box.width}px;height:{box.height}px;"
            f"font-size:{typography.font_size}px;line-height:{typography.line_height};"
            f"font-family:{font_family};"
            f"font-weight:{typography.font_weight};text-align:{block.align};color:{block.color};"
            "overflow:hidden;white-space:pre-wrap"
        )
        elements.append(f'<div data-id="{escape(block.id)}" style="{style}">{escape(block.text)}</div>')
    return "<!doctype html><meta charset=utf-8><style>html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#fff}*{box-sizing:border-box}body>*{position:absolute}</style><body>" + "".join(elements) + "</body>"
