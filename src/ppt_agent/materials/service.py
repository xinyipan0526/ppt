"""Artifact-first orchestration for source-material ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from ppt_agent.api.security import InputLimits
from ppt_agent.artifacts.store import ArtifactStore, CreateArtifact, EdgeInput
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.materials import MaterialErrorCode, MaterialStatus
from ppt_agent.materials.formats import MaterialFormat, sniff_material
from ppt_agent.materials.parsers import ParseRequest, ParserFailure
from ppt_agent.materials.store import MaterialSnapshot, MaterialStore
from ppt_agent.materials.worker import ParserWorker


class MaterialServiceError(RuntimeError):
    def __init__(self, code: MaterialErrorCode, safe_detail: str) -> None:
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail[:500]


class MaterialService:
    def __init__(
        self,
        store: MaterialStore,
        artifacts: ArtifactStore,
        worker: ParserWorker,
        limits: InputLimits,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.worker = worker
        self.limits = limits
        limits_payload = asdict(limits)
        encoded = json.dumps(limits_payload, sort_keys=True, separators=(",", ":")).encode()
        self._limits = {**limits_payload, "limits_fingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}

    async def upload(
        self,
        *,
        project_id: str,
        display_name: str,
        declared_type: str | None,
        chunks: AsyncIterable[bytes],
    ) -> MaterialSnapshot:
        iterator = chunks.__aiter__()
        prefix: list[bytes] = []
        head = bytearray()
        while len(head) < 65_536:
            try:
                chunk = await iterator.__anext__()
            except StopAsyncIteration:
                break
            if not isinstance(chunk, bytes):
                raise TypeError("upload chunks must be bytes")
            prefix.append(chunk)
            head.extend(chunk[: max(0, 65_536 - len(head))])
        if not head:
            raise MaterialServiceError(MaterialErrorCode.EMPTY_UPLOAD, "upload is empty")
        try:
            material_format = sniff_material(
                bytes(head), declared_type=declared_type, display_name=display_name
            )
        except ParserFailure as exc:
            raise MaterialServiceError(exc.code, exc.safe_detail) from exc
        material_id = str(uuid4())
        remaining = await asyncio.to_thread(
            self.store.remaining_upload_bytes, project_id
        )
        if remaining <= 0:
            raise MaterialServiceError(
                MaterialErrorCode.PROJECT_UPLOAD_LIMIT,
                "project upload byte limit exceeded",
            )
        safe_name = sanitize_display_name(display_name)
        staged = self.artifacts.create_staging(
            CreateArtifact(
                project_id=project_id,
                logical_id=f"source-material:{material_id}:source",
                kind="source_material",
                media_type=material_format.media_type,
                extension=material_format.extension,
                spec_fingerprint=f"source:{material_id}",
                metadata={"material_id": material_id, "display_name": safe_name},
            )
        )

        async def replay() -> AsyncIterator[bytes]:
            for value in prefix:
                yield value
            async for value in iterator:
                yield value

        try:
            written = await self.artifacts.write_staging_async(
                staged.id,
                replay(),
                max_bytes=min(self.limits.max_file_bytes, remaining),
            )
        except ValueError as exc:
            await asyncio.to_thread(self.artifacts.discard_staging, staged.id)
            code = (
                MaterialErrorCode.PROJECT_UPLOAD_LIMIT
                if remaining < self.limits.max_file_bytes
                else MaterialErrorCode.FILE_TOO_LARGE
            )
            raise MaterialServiceError(
                code,
                "project upload byte limit exceeded"
                if code is MaterialErrorCode.PROJECT_UPLOAD_LIMIT
                else "upload exceeds the single-file byte limit",
            ) from exc
        except BaseException:
            await asyncio.to_thread(self.artifacts.discard_staging, staged.id)
            raise
        if not written.size_bytes:
            raise MaterialServiceError(MaterialErrorCode.EMPTY_UPLOAD, "upload is empty")
        await asyncio.to_thread(self.artifacts.commit_file, staged.id)
        await asyncio.to_thread(self.artifacts.commit_metadata, staged.id)
        source = await asyncio.to_thread(self.artifacts.make_available, staged.id)
        try:
            return await asyncio.to_thread(
                self.store.create_uploaded,
                project_id=project_id,
                display_name=safe_name,
                media_type=material_format.media_type,
                source_artifact_id=source.id,
                source_bytes=int(source.size_bytes or 0),
                parser_name=material_format.parser_name,
                parser_version="1",
                limits=self._limits,
                limits_fingerprint=self._limits["limits_fingerprint"],
                material_id=material_id,
            )
        except Exception as exc:
            from ppt_agent.materials.store import MaterialQuotaExceeded

            if isinstance(exc, MaterialQuotaExceeded):
                await asyncio.to_thread(self.artifacts.mark_stale, source.id)
                raise MaterialServiceError(
                    MaterialErrorCode.PROJECT_UPLOAD_LIMIT,
                    "project upload byte limit exceeded",
                ) from exc
            raise

    async def parse(self, material_id: str) -> MaterialSnapshot:
        current = await asyncio.to_thread(self.store.get, material_id)
        if current.status is MaterialStatus.UPLOADED:
            claimed = await asyncio.to_thread(self.store.claim, material_id)
            if claimed is None:
                return await asyncio.to_thread(self.store.get, material_id)
        elif current.status is MaterialStatus.PARSING:
            claimed = current
        else:
            return current
        request = ParseRequest(
            material_id=claimed.id,
            source_artifact_id=claimed.source_artifact_id,
            source_path=str(await asyncio.to_thread(self.artifacts.path_for, claimed.source_artifact_id)),
            media_type=claimed.media_type,
            limits=dict(claimed.limits),
        )
        try:
            worker_task = asyncio.create_task(
                asyncio.to_thread(self.worker.parse, request),
                name=f"parser-worker:{claimed.id}",
            )
            try:
                parsed = await asyncio.shield(worker_task)
            except asyncio.CancelledError:
                await asyncio.to_thread(self.worker.cancel, claimed.id)
                try:
                    await worker_task
                except ParserFailure:
                    pass
                raise
            payload = parsed.model_dump_json(exclude_none=True).encode("utf-8")
            source = await asyncio.to_thread(self.artifacts.get, claimed.source_artifact_id)
            fingerprint_payload = {
                "material_id": claimed.id,
                "source_hash": source.content_hash,
                "parser": [claimed.parser_name, claimed.parser_version],
                "contract": 1,
                "limits": claimed.limits_fingerprint,
            }
            fingerprint = "sha256:" + hashlib.sha256(
                json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            staged = await asyncio.to_thread(
                self.artifacts.create_staging,
                CreateArtifact(
                    project_id=claimed.project_id,
                    logical_id=f"parsed-material:{claimed.id}:{claimed.attempt_count}",
                    kind="parsed_material",
                    media_type="application/json",
                    extension=".json",
                    spec_fingerprint=fingerprint,
                    metadata={
                        "material_id": claimed.id,
                        "attempt": claimed.attempt_count,
                        "schema_version": 1,
                    },
                    edges=(
                        EdgeInput(claimed.source_artifact_id, ArtifactEdgeType.DERIVED_FROM),
                    ),
                ),
            )
            await asyncio.to_thread(self.artifacts.write_staging, staged.id, [payload])
            await asyncio.to_thread(self.artifacts.commit_file, staged.id)
            await asyncio.to_thread(self.artifacts.commit_metadata, staged.id)
            available = await asyncio.to_thread(self.artifacts.make_available, staged.id)
            return await asyncio.to_thread(
                self.store.complete,
                claimed.id,
                expected_version=claimed.version,
                parsed_artifact_id=available.id,
            )
        except ParserFailure as exc:
            return await asyncio.to_thread(
                self.store.fail,
                claimed.id,
                expected_version=claimed.version,
                error_code=exc.code,
                safe_detail=exc.safe_detail,
            )
        except Exception:
            return await asyncio.to_thread(
                self.store.fail,
                claimed.id,
                expected_version=claimed.version,
                error_code=MaterialErrorCode.PARSER_CRASHED,
                safe_detail="material parsing failed unexpectedly",
            )

    async def retry(self, material_id: str, *, expected_version: int) -> MaterialSnapshot:
        return await asyncio.to_thread(
            self.store.retry, material_id, expected_version=expected_version
        )

    async def delete(self, material_id: str, *, expected_version: int) -> MaterialSnapshot:
        return await asyncio.to_thread(
            self.store.delete, material_id, expected_version=expected_version
        )


def sanitize_display_name(value: str) -> str:
    normalized = value.replace("\\", "/")
    name = PurePosixPath(normalized).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "material")[:255]
