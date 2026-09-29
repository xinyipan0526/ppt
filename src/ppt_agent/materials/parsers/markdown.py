"""Deterministic, non-rendering Markdown extraction."""

from __future__ import annotations

import re
from pathlib import Path

from ppt_agent.domain.materials import (
    ExternalReferenceV1,
    MaterialErrorCode,
    ParsedMaterialV1,
    SourceLocatorV1,
    TextBlockV1,
    build_text_block_id,
)
from ppt_agent.materials.parsers import PARSER_VERSION, ParseRequest, ParserFailure


_REFERENCE = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_LIST = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])\s+")


def parse_markdown(request: ParseRequest) -> ParsedMaterialV1:
    data = Path(request.source_path).read_bytes()
    try:
        source = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ParserFailure(MaterialErrorCode.INVALID_UTF8, "Markdown must be UTF-8") from exc
    max_characters = int(request.limits["max_markdown_characters"])
    if len(source) > max_characters:
        raise ParserFailure(MaterialErrorCode.FILE_TOO_LARGE, "Markdown character limit exceeded")

    blocks: list[TextBlockV1] = []
    references: list[ExternalReferenceV1] = []
    warnings: list[str] = []
    max_depth = int(request.limits["max_markdown_nesting"])
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        if not raw_line.strip():
            continue
        quote_prefix = re.match(r"^\s*((?:>\s*)+)", raw_line)
        quote_depth = quote_prefix.group(1).count(">") if quote_prefix else 0
        list_match = _LIST.match(raw_line)
        list_depth = (len(list_match.group(1).expandtabs(2)) // 2 + 1) if list_match else 0
        if max(quote_depth, list_depth) > max_depth:
            raise ParserFailure(
                MaterialErrorCode.MARKDOWN_TOO_DEEP,
                "Markdown nesting depth limit exceeded",
            )
        locator = SourceLocatorV1(
            kind="markdown", start_line=line_number, end_line=line_number
        )
        for match in _REFERENCE.finditer(raw_line):
            references.append(
                ExternalReferenceV1(
                    kind="image" if match.group(1) else "link",
                    target=match.group(2),
                    locator=locator,
                )
            )
        if len(references) > int(request.limits["max_external_resources"]):
            raise ParserFailure(
                MaterialErrorCode.EXTERNAL_RESOURCE_LIMIT,
                "external resource count limit exceeded",
            )
        if _HTML.search(raw_line) and "inert_html" not in warnings:
            warnings.append("inert_html")
        stripped = raw_line.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            kind = "heading"
            text = heading.group(1).strip()
        else:
            kind = "list" if list_match else "paragraph"
            text = stripped
        if text:
            blocks.append(
                TextBlockV1(
                    id=build_text_block_id(request.material_id, locator, text),
                    kind=kind,
                    text=text,
                    locator=locator,
                )
            )
    return ParsedMaterialV1(
        material_id=request.material_id,
        media_type="text/markdown",
        source_artifact_id=request.source_artifact_id,
        untrusted_source_content=True,
        text_blocks=tuple(blocks),
        external_references=tuple(references),
        warnings=tuple(warnings),
        parser_name="markdown",
        parser_version=PARSER_VERSION,
        limits_fingerprint=str(request.limits.get("limits_fingerprint", "limits:v1")),
    )
