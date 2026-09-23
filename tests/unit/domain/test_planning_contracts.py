import pytest
from pydantic import ValidationError

from ppt_agent.domain.contracts import (
    DeckOutline,
    OutlinePage,
    PageSpec,
    PageSpecs,
    PresentationBrief,
)


def _outline(count: int = 8) -> DeckOutline:
    return DeckOutline(
        narrative="A clear narrative",
        pages=[
            OutlinePage(page_index=index, objective=f"p{index}")
            for index in range(1, count + 1)
        ],
    )


def _pages(count: int = 8, *, outline: DeckOutline | None = None) -> PageSpecs:
    return PageSpecs(
        brief_page_count=count,
        outline=outline or _outline(count),
        pages=[
            PageSpec(page_index=index, objective=f"p{index}", page_type="content")
            for index in range(1, count + 1)
        ],
    )


def test_planning_contracts_accept_continuous_matching_pages() -> None:
    brief = PresentationBrief(topic="Roadmap", page_count=8)
    pages = _pages(brief.page_count)

    assert len(pages.pages) == brief.page_count


@pytest.mark.parametrize(
    "indexes",
    ([1, 1], [1, 3]),
)
def test_outline_rejects_duplicate_or_gapped_indexes(indexes: list[int]) -> None:
    with pytest.raises(ValidationError, match="continuous"):
        DeckOutline(
            narrative="Narrative",
            pages=[OutlinePage(page_index=i, objective=f"p{i}") for i in indexes],
        )


def test_page_specs_reject_count_and_objective_mismatch() -> None:
    with pytest.raises(ValidationError, match="brief page count"):
        _pages(7, outline=_outline(8))

    with pytest.raises(ValidationError, match="objective"):
        PageSpecs(
            brief_page_count=2,
            outline=_outline(2),
            pages=[
                PageSpec(page_index=1, objective="p1", page_type="cover"),
                PageSpec(page_index=2, objective="different", page_type="content"),
            ],
        )


def test_planning_contracts_are_strict_and_page_count_is_bounded() -> None:
    with pytest.raises(ValidationError, match="extra"):
        OutlinePage(page_index=1, objective="p1", unknown=True)
    with pytest.raises(ValidationError):
        PresentationBrief(topic="Too long", page_count=51)

