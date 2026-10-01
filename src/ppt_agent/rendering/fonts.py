"""Approved font discovery and stable render-environment identity."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


class MissingRequiredFont(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FontFile:
    path: Path
    name: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class FontSnapshot:
    chinese: tuple[FontFile, ...]
    latin: tuple[FontFile, ...]
    symbol: tuple[FontFile, ...]
    fingerprint: str


class FontRegistry:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        self._roots = roots

    def scan(self) -> FontSnapshot:
        fonts: list[FontFile] = []
        for root in self._roots:
            if not root.exists():
                continue
            for path in sorted(
                (item for item in root.rglob("*") if item.suffix.casefold() in {".ttf", ".otf", ".ttc"}),
                key=lambda item: str(item).casefold(),
            ):
                fonts.append(FontFile(path.resolve(), path.name, sha256(path.read_bytes()).hexdigest()))
        chinese_markers = (
            "cjk", "hans", "noto sans sc", "notosanssc", "sourcehansans",
            "yahei", "msyh", "simhei", "simsun",
        )
        chinese = tuple(item for item in fonts if any(marker in item.name.casefold() for marker in chinese_markers))
        if not chinese:
            raise MissingRequiredFont("no approved Chinese font fallback was found")
        latin = tuple(item for item in fonts if item not in chinese) or chinese
        symbol = tuple(item for item in fonts if "symbol" in item.name.casefold()) or latin
        identity = "\n".join(f"{item.name}:{item.content_hash}" for item in fonts)
        return FontSnapshot(chinese, latin, symbol, f"sha256:{sha256(identity.encode()).hexdigest()}")
