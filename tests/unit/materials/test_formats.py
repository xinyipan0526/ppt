import pytest

from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.formats import MaterialFormat, sniff_material
from ppt_agent.materials.parsers import ParserFailure


@pytest.mark.parametrize(
    ("head", "want"),
    [
        (b"%PDF-1.7\n", MaterialFormat.PDF),
        (b"\x89PNG\r\n\x1a\nrest", MaterialFormat.PNG),
        (b"\xff\xd8\xff\xe0rest", MaterialFormat.JPEG),
        (b"RIFF\x10\x00\x00\x00WEBPrest", MaterialFormat.WEBP),
        ("# 标题".encode(), MaterialFormat.MARKDOWN),
    ],
)
def test_sniff_material_uses_content_signatures(head: bytes, want: MaterialFormat) -> None:
    assert sniff_material(head, declared_type="application/octet-stream", display_name="wrong.bin") is want


def test_sniff_material_ignores_conflicting_declared_image_type() -> None:
    assert sniff_material(
        b"%PDF-1.7\n", declared_type="image/png", display_name="photo.png"
    ) is MaterialFormat.PDF


def test_sniff_material_rejects_binary_without_supported_signature() -> None:
    with pytest.raises(ParserFailure) as caught:
        sniff_material(b"\x00\x01\x02binary", declared_type="text/markdown", display_name="x.md")
    assert caught.value.code is MaterialErrorCode.UNSUPPORTED_MEDIA_TYPE
