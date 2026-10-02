"""Same-origin delivery for the packaged React application."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


_CSP = (
    "default-src 'self'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def packaged_asset_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def mount_web_app(app: FastAPI, *, asset_dir: Path | None = None) -> None:
    root = (asset_dir or packaged_asset_dir()).resolve()
    index = root / "index.html"

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    async def web_asset(asset_path: str):
        candidate = (root / "assets" / asset_path).resolve()
        try:
            candidate.relative_to(root / "assets")
        except ValueError:
            raise HTTPException(status_code=404, detail="web asset not found") from None
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="web asset not found")
        media_type, _ = mimetypes.guess_type(candidate.name)
        return FileResponse(
            candidate,
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def web_history_fallback(spa_path: str):
        if spa_path == "api" or spa_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "not found"})
        if not index.is_file():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "web_assets_missing",
                        "message": "packaged React assets are unavailable",
                    }
                },
            )
        return FileResponse(
            index,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": _CSP,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
