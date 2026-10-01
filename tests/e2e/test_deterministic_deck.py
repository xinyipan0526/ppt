import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pptx import Presentation

from ppt_agent.config.settings import load_settings
from ppt_agent.runtime.composition import build_application


def _wait_snapshot(client, headers, run_id, statuses, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=headers)
        assert response.status_code == 200, response.text
        snapshot = response.json()
        if snapshot["run"]["status"] in statuses:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


def test_no_key_text_to_eight_page_pptx(tmp_path: Path) -> None:
    token = "d" * 43
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path / "workspace"),
        "PPT_AGENT_PROVIDER_MODE": "deterministic",
        "PPT_AGENT_ACCESS_TOKEN": token,
    })
    app = build_application(settings)
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:8000"}
    with TestClient(app, base_url="http://localhost:8000") as client:
        project = client.post("/api/v1/projects", headers=headers, json={"name": "AI Report"}).json()
        planning_response = client.post(
            f"/api/v1/projects/{project['id']}/planning-runs", headers=headers,
            json={"normalized_text": "人工智能年度趋势报告"},
        )
        assert planning_response.status_code == 202, planning_response.text
        planning_id = planning_response.json()["run_id"]
        interrupted = _wait_snapshot(client, headers, planning_id, {"waiting_for_user", "failed", "recovery_required"})
        assert interrupted["run"]["status"] == "waiting_for_user", interrupted
        pending = next(item for item in interrupted["interrupts"] if item["status"] == "pending")
        resumed = client.post(
            f"/api/v1/runs/{planning_id}/interrupts/{pending['id']}/resume", headers=headers,
            json={"expected_version": pending["values"]["version"], "payload": {"action": "approve"}},
        )
        assert resumed.status_code == 202, resumed.text
        planned = _wait_snapshot(client, headers, planning_id, {"succeeded", "failed", "recovery_required"})
        assert planned["run"]["status"] == "succeeded", planned
        production_response = client.post(
            f"/api/v1/projects/{project['id']}/production-runs", headers=headers,
            json={"planning_run_id": planning_id},
        )
        assert production_response.status_code == 202, production_response.text
        production_id = production_response.json()["run_id"]
        produced = _wait_snapshot(client, headers, production_id, {"succeeded", "failed", "recovery_required"}, timeout=60)
        assert produced["run"]["status"] == "succeeded", produced["run"]["recovery_metadata"]
        assert len(produced["pages"]) == 8
        first_page = produced["pages"][0]
        edited = client.post(
            f"/api/v1/pages/{first_page['id']}/edits", headers=headers,
            json={
                "instruction": "Replace title with Updated AI Report",
                "idempotency_key": "e2e-title-edit-1",
            },
        )
        assert edited.status_code == 202, edited.text
        assert edited.json()["status"] == "succeeded"
        exported = client.post(f"/api/v1/runs/{production_id}/exports", headers=headers, json={"format": "pptx"})
        assert exported.status_code == 201, exported.text
        download = client.get(f"/api/v1/exports/{exported.json()['export_id']}/download", headers=headers)
        assert download.status_code == 200
        assert len(Presentation(BytesIO(download.content)).slides) == 8
        partial = client.get(
            f"/api/v1/exports/{exported.json()['export_id']}/download",
            headers={**headers, "Range": "bytes=0-99"},
        )
        assert partial.status_code == 206
        assert partial.headers["content-range"].startswith("bytes 0-99/")
        assert partial.content == download.content[:100]
