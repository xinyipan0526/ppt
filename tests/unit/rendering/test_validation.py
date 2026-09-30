from io import BytesIO

from PIL import Image

from ppt_agent.domain.production import DOMLayoutReportV1
from ppt_agent.rendering.validation import validate_page


def png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (1920, 1080), color).save(output, "PNG")
    return output.getvalue()


def test_validation_rejects_uniform_blank_page() -> None:
    report = validate_page("page_01", png((255, 255, 255)), DOMLayoutReportV1(page_id="page_01"))
    assert report.passed is False
    assert report.issues[0].code == "blank_page"


def test_validation_accepts_nonuniform_page() -> None:
    image = Image.new("RGB", (1920, 1080), "white")
    for x in range(800, 1100):
        for y in range(300, 600):
            image.putpixel((x, y), (10, 20, 30))
    output = BytesIO()
    image.save(output, "PNG")
    report = validate_page("page_01", output.getvalue(), DOMLayoutReportV1(page_id="page_01"))
    assert report.passed is True
