"""Bounded PDF text extraction."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from ppt_agent.domain.materials import (
    DocumentMetadataV1,
    MaterialErrorCode,
    ParsedMaterialV1,
    SourceLocatorV1,
    TextBlockV1,
    build_text_block_id,
)
from ppt_agent.materials.parsers import PARSER_VERSION, ParseRequest, ParserFailure


def _object_count(reader: PdfReader) -> int:
    count = sum(len(entries) for entries in getattr(reader, "xref", {}).values())
    count += len(getattr(reader, "xref_objStm", {}))
    return count


def parse_pdf(request: ParseRequest) -> ParsedMaterialV1:
    try:
        reader = PdfReader(Path(request.source_path), strict=True)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ParserFailure(MaterialErrorCode.PDF_ENCRYPTED, "PDF requires a password")
        page_count = len(reader.pages)
        if page_count > int(request.limits["max_pdf_pages"]):
            raise ParserFailure(MaterialErrorCode.PDF_PAGE_LIMIT, "PDF page limit exceeded")
        if _object_count(reader) > int(request.limits["max_pdf_objects"]):
            raise ParserFailure(MaterialErrorCode.PDF_OBJECT_LIMIT, "PDF object limit exceeded")
        blocks: list[TextBlockV1] = []
        warnings: list[str] = []
        remaining = int(request.limits["max_markdown_characters"])
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                warnings.append(f"page:{page_number}:text_unavailable")
                continue
            if len(text) > remaining:
                raise ParserFailure(MaterialErrorCode.FILE_TOO_LARGE, "extracted PDF text limit exceeded")
            remaining -= len(text)
            locator = SourceLocatorV1(kind="pdf", page=page_number)
            blocks.append(
                TextBlockV1(
                    id=build_text_block_id(request.material_id, locator, text),
                    kind="paragraph", text=text, locator=locator,
                )
            )
    except ParserFailure:
        raise
    except Exception as exc:
        raise ParserFailure(MaterialErrorCode.PDF_MALFORMED, "PDF cannot be parsed") from exc
    return ParsedMaterialV1(
        material_id=request.material_id, media_type="application/pdf",
        source_artifact_id=request.source_artifact_id, text_blocks=tuple(blocks),
        metadata=DocumentMetadataV1(page_count=page_count), warnings=tuple(warnings),
        parser_name="pdf", parser_version=PARSER_VERSION,
        limits_fingerprint=str(request.limits.get("limits_fingerprint", "limits:v1")),
    )
