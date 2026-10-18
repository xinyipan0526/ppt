"""16:9 image-only PDF exporter."""

from io import BytesIO

from PIL import Image


def export_pdf(pages: tuple[bytes, ...], *, pdf_dpi: int = 144) -> bytes:
    if not pages:
        raise ValueError("at least one page is required")
    images: list[Image.Image] = []
    try:
        for content in pages:
            image = Image.open(BytesIO(content)).convert("RGB")
            if image.size != (1920, 1080):
                image.close()
                raise ValueError("export pages must be 1920 x 1080")
            images.append(image)
        output = BytesIO()
        images[0].save(
            output, "PDF", save_all=True, append_images=images[1:], resolution=pdf_dpi,
            title="PPT Agent Export", author="PPT Agent", creator="PPT Agent",
        )
        return output.getvalue()
    finally:
        for image in images:
            image.close()
