from pathlib import Path

from fastapi.testclient import TestClient

from ppt_agent.config.settings import load_settings
from ppt_agent.runtime.composition import build_application


def test_deterministic_application_starts_with_authenticated_project_api(tmp_path: Path) -> None:
    settings = load_settings(None, environ={
        "PPT_AGENT_WORKSPACE": str(tmp_path / "workspace"),
        "PPT_AGENT_PROVIDER_MODE": "deterministic",
        "PPT_AGENT_ACCESS_TOKEN": "a" * 43,
    })
    app = build_application(settings)
    headers = {"Authorization": f"Bearer {'a' * 43}", "Origin": "http://localhost:8000"}
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/v1/ready", headers=headers).status_code == 200
        created = client.post("/api/v1/projects", headers=headers, json={"name": "Demo"})
        assert created.status_code == 201
        listed = client.get("/api/v1/projects", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["name"] == "Demo"
        providers = client.get("/api/v1/providers", headers=headers)
        assert providers.json()["mode"] == "deterministic"
