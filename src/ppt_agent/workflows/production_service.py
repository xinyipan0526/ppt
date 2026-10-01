"""Compile approved planning artifacts into a production run."""

from hashlib import sha256
import json

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.domain.contracts import PageSpec, PlanningBundle
from ppt_agent.domain.production import ProductionSpecV1
from ppt_agent.rendering.layout import compile_page
from ppt_agent.workflows.artifact_pipeline import commit_bytes
from ppt_agent.workflows.planning_store import PlanningStore
from ppt_agent.workflows.production_store import ProductionStore


class ProductionCoordinator:
    def __init__(self, planning: PlanningStore, production: ProductionStore, artifacts: ArtifactStore) -> None:
        self.planning = planning
        self.production = production
        self.artifacts = artifacts

    def create_from_planning(self, project_id: str, planning_run_id: str) -> str:
        run = self.planning.repository.get_run(planning_run_id)
        if run.project_id != project_id or run.status.value != "succeeded":
            raise ValueError("planning run must be approved and succeeded")
        refs = self.planning.available_stage_output_refs(planning_run_id, "review_plan")
        if len(refs) != 1:
            raise ValueError("approved planning bundle is unavailable")
        bundle_id = refs[0]
        bundle = PlanningBundle.model_validate(_payload(self.artifacts.read_bytes(bundle_id)))
        pages = tuple(
            compile_page(PageSpec.model_validate(_payload(self.artifacts.read_bytes(artifact_id))))
            for artifact_id in bundle.page_spec_artifact_ids
        )
        spec = ProductionSpecV1(
            project_id=project_id, planning_run_id=planning_run_id,
            planning_bundle_artifact_id=bundle_id,
            design_system_artifact_id=bundle.design_system_artifact_id,
            pages=pages,
        )
        payload = spec.model_dump_json().encode()
        artifact = commit_bytes(
            self.artifacts, project_id, f"production/spec/{planning_run_id}",
            "production.spec", "application/json", ".json", payload,
            f"sha256:{sha256(payload).hexdigest()}",
            source_artifact_ids=(bundle_id, bundle.design_system_artifact_id, *bundle.page_spec_artifact_ids),
        )
        return self.production.create_run(project_id, planning_run_id, bundle_id, artifact.id)


def _payload(content: bytes) -> dict:
    value = json.loads(content)
    if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
        raise ValueError("planning Artifact is not a versioned schema envelope")
    return value["payload"]
