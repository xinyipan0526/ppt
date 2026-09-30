"""Cheap deterministic checks for final page images and DOM layout reports."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageStat, UnidentifiedImageError

from ppt_agent.domain.production import DOMLayoutReportV1
from ppt_agent.domain.vision import QualityIssueV1, QualityReportV1


def validate_page(page_id: str, content: bytes, dom: DOMLayoutReportV1) -> QualityReportV1:
    issues: list[QualityIssueV1] = []
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            if image.size != (1920, 1080):
                issues.append(QualityIssueV1(code="artifact_invalid", severity="error", summary="page dimensions must be 1920 x 1080"))
            extrema = ImageStat.Stat(image.convert("RGB")).extrema
            if all(low == high for low, high in extrema):
                issues.append(QualityIssueV1(code="blank_page", severity="error", summary="page is visually uniform"))
    except (UnidentifiedImageError, OSError):
        issues.append(QualityIssueV1(code="artifact_invalid", severity="error", summary="page is not a decodable image"))
    for element in dom.elements:
        if element.overflowed:
            issues.append(QualityIssueV1(code="layout_overflow", severity="error", element_id=element.element_id, summary="text exceeded its declared box"))
    if dom.network_requests:
        issues.append(QualityIssueV1(code="artifact_invalid", severity="error", summary="renderer attempted network access"))
    return QualityReportV1(page_id=page_id, passed=not any(item.severity == "error" for item in issues), issues=tuple(issues))

