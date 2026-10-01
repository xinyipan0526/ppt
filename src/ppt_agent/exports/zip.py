"""Safe deterministic ZIP packaging."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def export_zip(pages: tuple[bytes, ...], extension: str) -> bytes:
    if extension not in {"png", "jpg"} or not pages:
        raise ValueError("a supported extension and pages are required")
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for index, content in enumerate(pages, 1):
            info = ZipInfo(f"slide-{index:03d}.{extension}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return output.getvalue()
