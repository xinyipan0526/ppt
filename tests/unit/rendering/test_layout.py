import pytest

from ppt_agent.domain.contracts import DeckOutline, OutlinePage, PageSpec, PageSpecs
from ppt_agent.rendering.layout import LayoutOverflow, compile_page


def test_compile_page_is_deterministic_and_inside_safe_area() -> None:
    source = PageSpec(
        page_index=1, objective="Introduce", page_type="cover", title="季度增长概览",
        body_blocks=("收入同比增长 25%",), visual_requirements=("blue abstract",),
    )
    first = compile_page(source)
    second = compile_page(source)
    assert first == second
    assert first.canvas.width == 1920
    assert all(block.box.x >= first.safe_area.left for block in first.text_blocks)
    assert first.render_mode == "hybrid"


def test_compile_page_rejects_unfit_delivery_text() -> None:
    source = PageSpec(
        page_index=1, objective="Dense", page_type="content", title="Title",
        body_blocks=("字" * 20_001,),
    )
    with pytest.raises(LayoutOverflow):
        compile_page(source)
