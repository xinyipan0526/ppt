"""Deterministic page production workflow."""

from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
from typing import Awaitable, Callable

from PIL import Image

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.artifacts import ArtifactEdgeType
from ppt_agent.domain.production import RenderManifestV1
from ppt_agent.domain.state_machine import PageStatus
from ppt_agent.rendering.chromium import ChromiumRenderer
from ppt_agent.rendering.validation import validate_page
from ppt_agent.providers.image import ImageGenerationResult
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.production_store import ProductionStore


VisualGenerator = Callable[[str, int, int], Awaitable[ImageGenerationResult]]


class DeckProductionWorkflow:
    def __init__(
        self,
        store: ProductionStore,
        artifacts: ArtifactStore,
        renderer: ChromiumRenderer,
        *,
        visual_generator: VisualGenerator | None = None,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.renderer = renderer
        self.visual_generator = visual_generator

    async def execute(self, run_id: str) -> None:
        spec = self.store.load_spec(run_id)
        self.store.start_run(run_id)
        snapshots = {item.page_index: item for item in self.store.current_pages(run_id)}
        for page in spec.pages:
            snapshot = snapshots[page.page_index]
            if snapshot.status is PageStatus.SUCCEEDED:
                continue
            self.store.start_page(snapshot.id, "rendering")
            visual_artifacts = []
            visual_urls: dict[str, str] = {}
            for region in page.visual_regions:
                if region.kind != "generated_image":
                    continue
                if self.visual_generator is None or not region.prompt:
                    raise ValueError(f"page {page.page_index} requires a visual generator")
                generated = await self.visual_generator(region.prompt, region.box.width, region.box.height)
                if generated.content is None:
                    raise ValueError("visual generator must return downloaded image content")
                visual = commit_bytes(
                    self.artifacts, spec.project_id,
                    f"production/{run_id}/page/{page.page_index}/visual/{region.id}",
                    "generated_visual", generated.media_type,
                    ".png" if generated.media_type == "image/png" else ".jpg",
                    generated.content,
                    f"sha256:{sha256(generated.content).hexdigest()}",
                    source_artifact_ids=tuple(region.source_artifact_ids),
                )
                visual_artifacts.append(visual)
                encoded = base64.b64encode(generated.content).decode("ascii")
                visual_urls[region.id] = f"data:{generated.media_type};base64,{encoded}"
            manifest = RenderManifestV1(
                page=page, renderer_version="html-css-svg-v1",
                chromium_version="playwright-managed", font_fingerprint="configured",
                visual_artifact_ids=tuple(item.id for item in visual_artifacts),
            )
            manifest_bytes = manifest.model_dump_json().encode()
            fingerprint = f"sha256:{sha256(manifest_bytes).hexdigest()}"
            manifest_artifact = commit_bytes(
                self.artifacts, spec.project_id, f"production/{run_id}/page/{page.page_index}/manifest",
                "render_manifest", "application/json", ".json", manifest_bytes, fingerprint,
                source_artifact_ids=(spec.planning_bundle_artifact_id, *(item.id for item in visual_artifacts)),
            )
            rendered = await self.renderer.render(manifest, visual_urls)
            dom_bytes = rendered.report.model_dump_json().encode()
            dom = commit_bytes(
                self.artifacts, spec.project_id, f"production/{run_id}/page/{page.page_index}/dom",
                "dom_layout_report", "application/json", ".json", dom_bytes,
                f"{fingerprint}:dom", source_artifact_ids=(manifest_artifact.id,),
            )
            page_image = commit_bytes(
                self.artifacts, spec.project_id, f"production/{run_id}/page/{page.page_index}/image",
                "page_image", "image/png", ".png", rendered.png,
                f"{fingerprint}:png", source_artifact_ids=(manifest_artifact.id, dom.id),
                edge_type=ArtifactEdgeType.COMPOSED_FROM,
            )
            with Image.open(BytesIO(rendered.png)) as image:
                image.thumbnail((480, 270))
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
                thumbnail_bytes = output.getvalue()
            thumbnail = commit_bytes(
                self.artifacts, spec.project_id,
                f"production/{run_id}/page/{page.page_index}/thumbnail",
                "page_thumbnail", "image/jpeg", ".jpg", thumbnail_bytes,
                f"{fingerprint}:thumbnail", source_artifact_ids=(page_image.id,),
                edge_type=ArtifactEdgeType.DERIVED_FROM,
            )
            self.store.advance_page(snapshot.id, "validation")
            quality = validate_page(page.page_id, rendered.png, rendered.report)
            quality_bytes = quality.model_dump_json().encode()
            quality_artifact = commit_bytes(
                self.artifacts, spec.project_id, f"production/{run_id}/page/{page.page_index}/quality",
                "quality_report", "application/json", ".json", quality_bytes,
                f"{fingerprint}:quality", source_artifact_ids=(page_image.id,),
                edge_type=ArtifactEdgeType.VALIDATES,
            )
            if not quality.passed:
                raise ValueError(f"page {page.page_index} failed quality validation")
            self.store.complete_page(snapshot.id, snapshot.generation_id, page_image.id, {
                "visual": visual_artifacts[0].id if visual_artifacts else None,
                "manifest": manifest_artifact.id, "dom": dom.id,
                "thumbnail": thumbnail.id, "quality": quality_artifact.id,
            }, quality_passed=quality.passed, quality_issues=[item.model_dump() for item in quality.issues])
        self.store.complete_run(run_id)
