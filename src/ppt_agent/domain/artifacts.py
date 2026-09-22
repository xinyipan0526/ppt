"""Artifact identities, lifecycle states, and dependency semantics."""

from enum import StrEnum


class ArtifactStatus(StrEnum):
    STAGING = "staging"
    FILE_COMMITTED = "file_committed"
    METADATA_COMMITTED = "metadata_committed"
    AVAILABLE = "available"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALID = "invalid"
    DELETED = "deleted"


class ArtifactEdgeType(StrEnum):
    DERIVED_FROM = "derived_from"
    REFERENCES = "references"
    SUPERSEDES = "supersedes"
    COMPOSED_FROM = "composed_from"
    VALIDATES = "validates"


CONTENT_DEPENDENCY_EDGES = frozenset(
    {ArtifactEdgeType.DERIVED_FROM, ArtifactEdgeType.COMPOSED_FROM}
)

