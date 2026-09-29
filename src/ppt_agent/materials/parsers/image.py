"""Bounded image metadata extraction and full decode verification."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence, UnidentifiedImageError

from ppt_agent.domain.materials import DocumentMetadataV1, MaterialErrorCode, ParsedMaterialV1
from ppt_agent.materials.parsers import PARSER_VERSION, ParseRequest, ParserFailure


_MEDIA_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


def parse_image(request: ParseRequest) -> ParsedMaterialV1:
    path = Path(request.source_path)
    try:
        with Image.open(path) as opened:
            image_format = str(opened.format or "").upper()
            if image_format != _MEDIA_FORMATS.get(request.media_type):
                raise ParserFailure(
                    MaterialErrorCode.IMAGE_DECODE_FAILED,
                    "decoded image format does not match sniffed media type",
                )
            width, height = opened.size
            frame_count = int(getattr(opened, "n_frames", 1))
            if width > int(request.limits["max_image_width"]) or height > int(
                request.limits["max_image_height"]
            ):
                raise ParserFailure(MaterialErrorCode.IMAGE_PIXEL_LIMIT, "image dimensions exceed limit")
            if width * height > int(request.limits["max_image_pixels"]):
                raise ParserFailure(MaterialErrorCode.IMAGE_PIXEL_LIMIT, "image pixel limit exceeded")
            if frame_count > int(request.limits["max_image_frames"]):
                raise ParserFailure(MaterialErrorCode.IMAGE_FRAME_LIMIT, "image frame limit exceeded")
            decoded_bytes = 0
            for frame in ImageSequence.Iterator(opened):
                frame_width, frame_height = frame.size
                if frame_width > int(request.limits["max_image_width"]) or frame_height > int(
                    request.limits["max_image_height"]
                ):
                    raise ParserFailure(
                        MaterialErrorCode.IMAGE_PIXEL_LIMIT, "image dimensions exceed limit"
                    )
                mode = frame.mode
                sample_bytes = 4 if mode in {"I", "F"} else 2 if "16" in mode else 1
                decoded_bytes += frame_width * frame_height * max(
                    1, len(frame.getbands())
                ) * sample_bytes
                if decoded_bytes > int(request.limits["max_decoded_image_bytes"]):
                    raise ParserFailure(
                        MaterialErrorCode.IMAGE_PIXEL_LIMIT,
                        "decoded image byte limit exceeded",
                    )
                frame.load()
            compressed_bytes = max(1, path.stat().st_size)
            if decoded_bytes / compressed_bytes > float(request.limits["max_compression_ratio"]):
                raise ParserFailure(
                    MaterialErrorCode.IMAGE_COMPRESSION_LIMIT,
                    "image compression ratio limit exceeded",
                )
            mode = opened.mode
        with Image.open(path) as verified:
            verified.verify()
    except ParserFailure:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ParserFailure(MaterialErrorCode.IMAGE_DECODE_FAILED, "image cannot be decoded") from exc
    return ParsedMaterialV1(
        material_id=request.material_id, media_type=request.media_type,
        source_artifact_id=request.source_artifact_id,
        untrusted_source_content=True,
        metadata=DocumentMetadataV1(
            image_width=width, image_height=height, image_format=image_format,
            image_mode=mode, frame_count=frame_count,
        ),
        parser_name="image", parser_version=PARSER_VERSION,
        limits_fingerprint=str(request.limits.get("limits_fingerprint", "limits:v1")),
    )
