from dataclasses import asdict, replace
from pathlib import Path

import pytest

from ppt_agent.api.security import InputLimits
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.parsers.markdown import parse_markdown


def request(path: Path, limits: InputLimits | None = None) -> ParseRequest:
    return ParseRequest(
        material_id="material-1",
        source_artifact_id="artifact-1",
        source_path=str(path),
        media_type="text/markdown",
        limits=asdict(limits or InputLimits()),
    )


def test_markdown_parser_preserves_lines_and_records_external_refs_without_fetching(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text(
        "# Title\n\nParagraph [source](https://example.invalid/report).\n\n"
        "![chart](https://example.invalid/chart.png)\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )

    parsed = parse_markdown(request(path))

    assert [(block.kind, block.locator.start_line) for block in parsed.text_blocks] == [
        ("heading", 1), ("paragraph", 3), ("paragraph", 5), ("paragraph", 6)
    ]
    assert [ref.target for ref in parsed.external_references] == [
        "https://example.invalid/report", "https://example.invalid/chart.png"
    ]
    assert "inert_html" in parsed.warnings
    assert parsed.untrusted_source_content is True


def test_markdown_parser_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(ParserFailure) as caught:
        parse_markdown(request(path))
    assert caught.value.code is MaterialErrorCode.INVALID_UTF8


def test_markdown_parser_enforces_nesting_and_external_resource_limits(tmp_path: Path) -> None:
    deep = tmp_path / "deep.md"
    deep.write_text("> " * 4 + "too deep", encoding="utf-8")
    limits = replace(InputLimits(), max_markdown_nesting=3)
    with pytest.raises(ParserFailure) as caught:
        parse_markdown(request(deep, limits))
    assert caught.value.code is MaterialErrorCode.MARKDOWN_TOO_DEEP

    refs = tmp_path / "refs.md"
    refs.write_text("[a](https://a.invalid) [b](https://b.invalid)", encoding="utf-8")
    limits = replace(InputLimits(), max_external_resources=1)
    with pytest.raises(ParserFailure) as caught:
        parse_markdown(request(refs, limits))
    assert caught.value.code is MaterialErrorCode.EXTERNAL_RESOURCE_LIMIT
