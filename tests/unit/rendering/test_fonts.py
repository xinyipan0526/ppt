from pathlib import Path

import pytest

from ppt_agent.rendering.fonts import FontRegistry, MissingRequiredFont


def test_font_registry_hash_is_stable_and_requires_chinese_fallback(tmp_path: Path) -> None:
    (tmp_path / "NotoSansCJK-Regular.ttf").write_bytes(b"font-a")
    first = FontRegistry((tmp_path,)).scan()
    second = FontRegistry((tmp_path,)).scan()
    assert first.fingerprint == second.fingerprint
    assert first.chinese[0].name == "NotoSansCJK-Regular.ttf"


def test_font_registry_rejects_missing_chinese_font(tmp_path: Path) -> None:
    (tmp_path / "Arial.ttf").write_bytes(b"font")
    with pytest.raises(MissingRequiredFont):
        FontRegistry((tmp_path,)).scan()
