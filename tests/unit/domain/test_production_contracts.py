import pytest
from pydantic import ValidationError

from ppt_agent.domain.exports import ExportManifestV1, ExportPageV1
from ppt_agent.domain.production import (
    CanvasV1,
    ProductionPageSpecV1,
    ProductionSpecV1,
    SafeAreaV1,
    TextBlockV1,
    TypographyV1,
)
from ppt_agent.domain.vision import NormalizedBoxV1, PageEditPlanV1


def page(page_index: int = 1) -> ProductionPageSpecV1:
    return ProductionPageSpecV1(
        page_id=f"page_{page_index:02d}",
        page_index=page_index,
        render_mode="deterministic",
        canvas=CanvasV1(),
        safe_area=SafeAreaV1(),
        layout_token="title_body",
        text_blocks=(
            TextBlockV1(
                id="title",
                role="title",
                text="季度增长概览",
                box={"x": 96, "y": 120, "width": 760, "height": 180},
                typography=TypographyV1(
                    font_family_token="heading",
                    font_size=68,
                    min_font_size=48,
                    line_height=1.15,
                    font_weight=700,
                ),
                max_lines=2,
            ),
        ),
    )


def test_production_page_rejects_nonstandard_canvas() -> None:
    with pytest.raises(ValidationError):
        ProductionPageSpecV1.model_validate(
            {**page().model_dump(), "canvas": {"width": 1280, "height": 720}}
        )


def test_production_page_rejects_text_outside_safe_area() -> None:
    payload = page().model_dump()
    payload["text_blocks"][0]["box"] = {
        "x": 10,
        "y": 10,
        "width": 300,
        "height": 100,
    }
    with pytest.raises(ValidationError, match="safe area"):
        ProductionPageSpecV1.model_validate(payload)


def test_production_spec_requires_continuous_page_indexes() -> None:
    with pytest.raises(ValidationError, match="continuous"):
        ProductionSpecV1(
            project_id="project",
            planning_run_id="planning",
            planning_bundle_artifact_id="bundle",
            design_system_artifact_id="design",
            pages=(page(1), page(3)),
        )


def test_normalized_box_rejects_coordinates_over_one() -> None:
    with pytest.raises(ValidationError):
        NormalizedBoxV1(x=0.2, y=0.2, width=0.9, height=0.2)


def test_visual_edit_requires_a_target_box() -> None:
    with pytest.raises(ValidationError, match="target_box"):
        PageEditPlanV1(kind="visual_edit", instruction="remove the logo")


def test_export_manifest_requires_continuous_ordered_pages() -> None:
    with pytest.raises(ValidationError, match="continuous"):
        ExportManifestV1(
            project_id="project",
            production_run_id="run",
            format="pptx",
            pages=(
                ExportPageV1(page_index=1, artifact_id="a", content_hash="sha256:a"),
                ExportPageV1(page_index=3, artifact_id="c", content_hash="sha256:c"),
            ),
            exporter_version="1",
        )
