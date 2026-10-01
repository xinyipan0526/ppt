"""Compile natural-language page edits into a bounded, versioned plan."""

from __future__ import annotations

import re

from ppt_agent.domain.production import ProductionPageSpecV1
from ppt_agent.domain.vision import NormalizedBoxV1, PageEditPlanV1, PageEditRequestV1


_EXECUTABLE = re.compile(r"<\s*script|javascript:|\b(?:eval|exec|powershell|cmd\.exe)\b", re.IGNORECASE)


def compile_edit_plan(
    request: PageEditRequestV1,
    page: ProductionPageSpecV1,
    *,
    target_box: NormalizedBoxV1 | None = None,
) -> PageEditPlanV1:
    instruction = request.instruction.strip()
    if _EXECUTABLE.search(instruction):
        raise ValueError("executable instructions are not accepted")
    lowered = instruction.casefold()
    full_page = ("整页", "整张", "full page", "entire page", "regenerate page")
    style = ("颜色", "字号", "字体", "排版", "样式", "配色", "color", "font", "layout", "style")
    content = ("改成", "替换", "文案", "标题", "正文", "文字", "replace", "rewrite", "title", "copy")
    visual = ("图片", "插图", "背景", "image", "visual", "background")
    if any(token in lowered for token in full_page):
        return PageEditPlanV1(kind="full_page_regeneration", instruction=instruction)
    if any(token in lowered for token in visual):
        if target_box is not None:
            return PageEditPlanV1(kind="visual_edit", instruction=instruction, target_box=target_box)
        return PageEditPlanV1(
            kind="ambiguous", instruction=instruction,
            clarification_question="请在页面中明确要修改的图片区域。",
        )
    if any(token in lowered for token in style):
        targets = tuple(block.id for block in page.text_blocks if block.id in lowered or block.role in lowered)
        return PageEditPlanV1(kind="style_edit", instruction=instruction, target_element_ids=targets)
    if any(token in lowered for token in content):
        targets = tuple(block.id for block in page.text_blocks if block.id in lowered or block.role in lowered)
        return PageEditPlanV1(kind="content_edit", instruction=instruction, target_element_ids=targets)
    return PageEditPlanV1(
        kind="ambiguous", instruction=instruction,
        clarification_question="请说明要修改文案、样式、图片，还是重新生成整页。",
    )
