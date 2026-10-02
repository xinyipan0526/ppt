from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ppt_agent.web import mount_web_app
from ppt_agent.config.settings import Settings
from ppt_agent.runtime.composition import build_application


def test_web_app_serves_spa_assets_and_preserves_api_404(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<html><body>product-shell</body></html>", encoding="utf-8")
    (assets / "assets" / "app-abc.js").write_text("console.log('app')", encoding="utf-8")
    app = FastAPI()
    mount_web_app(app, asset_dir=assets)

    with TestClient(app) as client:
        root = client.get("/")
        history = client.get("/projects/project-1")
        script = client.get("/assets/app-abc.js")
        missing_api = client.get("/api/v1/not-a-route")

    assert root.status_code == 200 and "product-shell" in root.text
    assert history.status_code == 200 and "product-shell" in history.text
    assert "default-src 'self'" in root.headers["content-security-policy"]
    assert script.status_code == 200
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")


def test_web_app_reports_missing_packaged_assets(tmp_path: Path) -> None:
    app = FastAPI()
    mount_web_app(app, asset_dir=tmp_path / "missing")
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "web_assets_missing"


def test_complete_application_serves_packaged_product_without_shadowing_api(
    tmp_path: Path,
) -> None:
    app = build_application(
        Settings(workspace=tmp_path / "workspace", local_access_token="t" * 43)
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        root = client.get("/")
        api_missing = client.get(
            "/api/v1/not-a-route", headers={"Authorization": "Bearer " + "t" * 43}
        )
    assert root.status_code == 200
    assert '<div id="root"></div>' in root.text
    assert api_missing.status_code == 404
    assert "<html" not in api_missing.text
