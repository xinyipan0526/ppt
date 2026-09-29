from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pypdf import PdfWriter

from ppt_agent.api.security import InputLimits
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.parsers.pdf import parse_pdf


def make_pdf(path: Path, *, pages: int = 1, password: str | None = None) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=100)
    if password:
        writer.encrypt(password)
    with path.open("wb") as output:
        writer.write(output)


def request(path: Path, limits: InputLimits | None = None) -> ParseRequest:
    return ParseRequest(
        material_id="material-pdf", source_artifact_id="artifact-pdf",
        source_path=str(path), media_type="application/pdf",
        limits=asdict(limits or InputLimits()),
    )


def test_pdf_parser_records_blank_pages_as_text_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    make_pdf(path, pages=2)

    parsed = parse_pdf(request(path))

    assert parsed.metadata.page_count == 2
    assert parsed.text_blocks == ()
    assert parsed.warnings == ("page:1:text_unavailable", "page:2:text_unavailable")


def test_pdf_parser_enforces_page_and_object_limits(tmp_path: Path) -> None:
    path = tmp_path / "two.pdf"
    make_pdf(path, pages=2)
    with pytest.raises(ParserFailure) as caught:
        parse_pdf(request(path, replace(InputLimits(), max_pdf_pages=1)))
    assert caught.value.code is MaterialErrorCode.PDF_PAGE_LIMIT

    with pytest.raises(ParserFailure) as caught:
        parse_pdf(request(path, replace(InputLimits(), max_pdf_objects=1)))
    assert caught.value.code is MaterialErrorCode.PDF_OBJECT_LIMIT


def test_pdf_parser_rejects_encrypted_and_malformed_files(tmp_path: Path) -> None:
    encrypted = tmp_path / "locked.pdf"
    make_pdf(encrypted, password="secret")
    with pytest.raises(ParserFailure) as caught:
        parse_pdf(request(encrypted))
    assert caught.value.code is MaterialErrorCode.PDF_ENCRYPTED

    malformed = tmp_path / "broken.pdf"
    malformed.write_bytes(b"%PDF-1.7\nnot a pdf")
    with pytest.raises(ParserFailure) as caught:
        parse_pdf(request(malformed))
    assert caught.value.code is MaterialErrorCode.PDF_MALFORMED
