"""Shared contracts for local material parsers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ppt_agent.domain.materials import MaterialErrorCode


class ParserFailure(RuntimeError):
    def __init__(self, code: MaterialErrorCode, safe_detail: str) -> None:
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail[:500]


@dataclass(frozen=True, slots=True)
class ParseRequest:
    material_id: str
    source_artifact_id: str
    source_path: str
    media_type: str
    limits: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "source_artifact_id": self.source_artifact_id,
            "source_path": self.source_path,
            "media_type": self.media_type,
            "limits": dict(self.limits),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ParseRequest":
        return cls(
            material_id=str(payload["material_id"]),
            source_artifact_id=str(payload["source_artifact_id"]),
            source_path=str(payload["source_path"]),
            media_type=str(payload["media_type"]),
            limits=dict(payload["limits"]),
        )


PARSER_VERSION = "1"
