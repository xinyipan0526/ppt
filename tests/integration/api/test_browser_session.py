from fastapi import Depends
from fastapi.testclient import TestClient

from ppt_agent.api.app import create_app
from ppt_agent.api.security import LocalHTTPSecurity


def test_browser_session_uses_httponly_cookie_and_requires_csrf_for_mutation() -> None:
    security = LocalHTTPSecurity(token="t" * 43)
    app = create_app(security=security)

    @app.post("/api/v1/browser-protected", dependencies=[Depends(security.authenticate)])
    async def browser_protected() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, base_url="http://localhost:8000") as client:
        session = client.get("/api/v1/session")
        csrf = session.json()["csrf_token"]
        cookie = session.headers["set-cookie"]
        rejected = client.post(
            "/api/v1/browser-protected", headers={"Origin": "http://localhost:8000"}
        )
        accepted = client.post(
            "/api/v1/browser-protected",
            headers={"Origin": "http://localhost:8000", "X-CSRF-Token": csrf},
        )

    assert session.status_code == 200
    assert "t" * 43 not in session.text
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert rejected.status_code == 403
    assert accepted.status_code == 200


def test_bearer_automation_does_not_require_csrf() -> None:
    security = LocalHTTPSecurity(token="t" * 43)
    app = create_app(security=security)

    @app.post("/api/v1/bearer-protected", dependencies=[Depends(security.authenticate)])
    async def bearer_protected() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post(
            "/api/v1/bearer-protected",
            headers={
                "Origin": "http://localhost:8000",
                "Authorization": "Bearer " + "t" * 43,
            },
        )

    assert response.status_code == 200
