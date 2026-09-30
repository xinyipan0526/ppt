"""Small idempotent helpers for committing production Artifacts."""

from __future__ import annotations

from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact, EdgeInput, ArtifactSnapshot
from ppt_agent.domain.artifacts import ArtifactEdgeType


def commit_bytes(
    artifacts: ArtifactStore,
    project_id: str,
    logical_id: str,
    kind: str,
    media_type: str,
    extension: str,
    content: bytes,
    fingerprint: str,
    *,
    source_artifact_ids: tuple[str, ...] = (),
    edge_type: ArtifactEdgeType = ArtifactEdgeType.DERIVED_FROM,
    operation_id: str | None = None,
    metadata: dict | None = None,
) -> ArtifactSnapshot:
    cached = artifacts.find_cache_hit(project_id, kind, fingerprint)
    if cached is not None:
        return cached
    record = artifacts.create_staging(CreateArtifact(
        project_id=project_id, logical_id=logical_id, kind=kind,
        media_type=media_type, extension=extension, spec_fingerprint=fingerprint,
        metadata=dict(metadata or {}), operation_id=operation_id,
        edges=tuple(EdgeInput(source, edge_type) for source in source_artifact_ids),
    ))
    artifacts.write_staging(record.id, [content])
    artifacts.commit_file(record.id)
    artifacts.commit_metadata(record.id)
    return artifacts.make_available(record.id)

