"""Image format conversion."""

from io import BytesIO
from typing import Literal

from PIL import Image


def convert_pages(pages: tuple[bytes, ...], output_format: Literal["png", "jpg"]) -> tuple[bytes, ...]:
    if not pages:
        raise ValueError("at least one page is required")
    result: list[bytes] = []
    for content in pages:
        with Image.open(BytesIO(content)) as image:
            if image.size != (1920, 1080):
                raise ValueError("export pages must be 1920 x 1080")
            buffer = BytesIO()
            if output_format == "png":
                image.convert("RGB").save(buffer, "PNG", optimize=True)
            elif output_format == "jpg":
                image.convert("RGB").save(buffer, "JPEG", quality=92, optimize=True)
            else:
                raise ValueError("unsupported image export format")
            result.append(buffer.getvalue())
    return tuple(result)
