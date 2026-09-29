import pytest
from pydantic import ValidationError

from ppt_agent.domain.materials import (
    ParsedMaterialV1,
    SourceLocatorV1,
    TextBlockV1,
    build_text_block_id,
)


def locator() -> SourceLocatorV1:
    return SourceLocatorV1(kind="markdown", start_line=2, end_line=4)


def test_text_block_id_is_deterministic_and_content_sensitive() -> None:
    first = build_text_block_id("material-1", locator(), "Quarterly results")

    assert first == build_text_block_id("material-1", locator(), "Quarterly results")
    assert first.startswith("block_")
    assert first != build_text_block_id("material-1", locator(), "Other results")


def test_parsed_material_requires_untrusted_source_marker() -> None:
    block = TextBlockV1(
        id="block_1234567890abcdef",
        kind="paragraph",
        text="source text",
        locator=locator(),
    )

    with pytest.raises(ValidationError):
        ParsedMaterialV1(
            material_id="material-1",
            media_type="text/markdown",
            source_artifact_id="artifact-1",
            untrusted_source_content=False,
            text_blocks=(block,),
            parser_name="markdown",
            parser_version="1",
            limits_fingerprint="sha256:limits",
        )


def test_parsed_material_round_trips_strict_versioned_json() -> None:
    block = TextBlockV1(
        id="block_1234567890abcdef",
        kind="heading",
        text="Title",
        locator=SourceLocatorV1(kind="markdown", start_line=1, end_line=1),
    )
    parsed = ParsedMaterialV1(
        material_id="material-1",
        media_type="text/markdown",
        source_artifact_id="artifact-1",
        text_blocks=(block,),
        parser_name="markdown",
        parser_version="1",
        limits_fingerprint="sha256:limits",
    )

    restored = ParsedMaterialV1.model_validate_json(parsed.model_dump_json())

    assert restored == parsed
    assert ParsedMaterialV1.model_json_schema()["properties"]["schema_version"]["const"] == 1
    with pytest.raises(ValidationError):
        ParsedMaterialV1.model_validate({**parsed.model_dump(), "unexpected": True})


def test_locator_rejects_fields_from_another_media_kind() -> None:
    with pytest.raises(ValidationError):
        SourceLocatorV1(kind="pdf", page=1, start_line=1)
