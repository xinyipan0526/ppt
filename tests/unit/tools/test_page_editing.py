import pytest

from ppt_agent.domain.production import BoxV1, ProductionPageSpecV1, TextBlockV1, TypographyV1
from ppt_agent.domain.vision import PageEditRequestV1
from ppt_agent.tools.page_editing import compile_edit_plan


@pytest.fixture
def page() -> ProductionPageSpecV1:
    return ProductionPageSpecV1(
        page_id="page-1", page_index=1, render_mode="deterministic", layout_token="title_body",
        text_blocks=(TextBlockV1(
            id="title", role="title", text="Old", box=BoxV1(x=72, y=64, width=900, height=160),
            typography=TypographyV1(font_family_token="heading", font_size=64, min_font_size=36, line_height=1.1, font_weight=700),
            max_lines=2,
        ),),
    )


@pytest.mark.parametrize(("instruction", "kind"), [
    ("把标题改成年度总结", "content_edit"),
    ("将标题颜色和字号调得更醒目", "style_edit"),
    ("重新生成整页", "full_page_regeneration"),
    ("Make it better", "ambiguous"),
])
def test_compile_edit_plan_classifies_bounded_edits(page, instruction, kind) -> None:
    plan = compile_edit_plan(PageEditRequestV1(instruction=instruction), page)
    assert plan.kind == kind
    if kind == "ambiguous":
        assert plan.clarification_question


def test_visual_edit_requires_normalized_target(page) -> None:
    plan = compile_edit_plan(PageEditRequestV1(instruction="修改右侧图片"), page)
    assert plan.kind == "ambiguous"


def test_rejects_executable_instruction(page) -> None:
    with pytest.raises(ValueError, match="executable"):
        compile_edit_plan(PageEditRequestV1(instruction="run <script>alert(1)</script>"), page)
