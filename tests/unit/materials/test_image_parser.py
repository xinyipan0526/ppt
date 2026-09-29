from dataclasses import asdict, replace
from pathlib import Path

import pytest
from PIL import Image

from ppt_agent.api.security import InputLimits
from ppt_agent.domain.materials import MaterialErrorCode
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.parsers.image import parse_image


def request(path: Path, media_type: str = "image/png", limits: InputLimits | None = None) -> ParseRequest:
    return ParseRequest(
        material_id="material-image", source_artifact_id="artifact-image",
        source_path=str(path), media_type=media_type,
        limits=asdict(limits or InputLimits()),
    )


def test_image_parser_fully_decodes_and_returns_bounded_metadata(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (8, 6), (10, 20, 30)).save(path)

    parsed = parse_image(request(path))

    assert parsed.metadata.image_width == 8
    assert parsed.metadata.image_height == 6
    assert parsed.metadata.image_format == "PNG"
    assert parsed.metadata.frame_count == 1
    assert parsed.text_blocks == ()


def test_image_parser_enforces_pixel_and_compression_limits(tmp_path: Path) -> None:
    path = tmp_path / "solid.png"
    Image.new("RGB", (100, 100), "white").save(path)
    with pytest.raises(ParserFailure) as caught:
        parse_image(request(path, limits=replace(InputLimits(), max_image_pixels=9_999)))
    assert caught.value.code is MaterialErrorCode.IMAGE_PIXEL_LIMIT

    with pytest.raises(ParserFailure) as caught:
        parse_image(request(path, limits=replace(InputLimits(), max_compression_ratio=1.0)))
    assert caught.value.code is MaterialErrorCode.IMAGE_COMPRESSION_LIMIT


def test_image_parser_rejects_animated_and_truncated_images(tmp_path: Path) -> None:
    animated = tmp_path / "animated.webp"
    frames = [Image.new("RGB", (4, 4), color) for color in ("red", "blue")]
    frames[0].save(animated, save_all=True, append_images=frames[1:], duration=100, loop=0, format="WEBP")
    with pytest.raises(ParserFailure) as caught:
        parse_image(request(animated, media_type="image/webp"))
    assert caught.value.code is MaterialErrorCode.IMAGE_FRAME_LIMIT

    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
    with pytest.raises(ParserFailure) as caught:
        parse_image(request(truncated))
    assert caught.value.code is MaterialErrorCode.IMAGE_DECODE_FAILED
