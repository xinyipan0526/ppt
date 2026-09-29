"""Content-first source material identification."""

from __future__ import annotations

from enum import StrEnum

from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.parsers import ParserFailure


class MaterialFormat(StrEnum):
    MARKDOWN = "markdown"
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"

    @property
    def media_type(self) -> str:
        return {
            MaterialFormat.MARKDOWN: "text/markdown",
            MaterialFormat.PDF: "application/pdf",
            MaterialFormat.PNG: "image/png",
            MaterialFormat.JPEG: "image/jpeg",
            MaterialFormat.WEBP: "image/webp",
        }[self]

    @property
    def extension(self) -> str:
        return ".md" if self is MaterialFormat.MARKDOWN else f".{self.value}"

    @property
    def parser_name(self) -> str:
        if self is MaterialFormat.MARKDOWN:
            return "markdown"
        if self is MaterialFormat.PDF:
            return "pdf"
        return "image"


def sniff_material(
    head: bytes, *, declared_type: str | None, display_name: str
) -> MaterialFormat:
    del declared_type, display_name
    if head.startswith(b"%PDF-"):
        return MaterialFormat.PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MaterialFormat.PNG
    if head.startswith(b"\xff\xd8\xff"):
        return MaterialFormat.JPEG
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return MaterialFormat.WEBP
    if b"\x00" not in head:
        try:
            head.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            return MaterialFormat.MARKDOWN
    raise ParserFailure(
        MaterialErrorCode.UNSUPPORTED_MEDIA_TYPE,
        "source content is not a supported material format",
    )
