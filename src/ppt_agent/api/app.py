"""FastAPI application factory."""

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from ppt_agent import __version__


class HealthResponse(BaseModel):
    """Stable response returned by the liveness endpoint."""

    status: str
    service: str
    version: str


def create_app() -> FastAPI:
    """Create an application instance without import-time side effects."""

    app = FastAPI(title="PPT Agent API", version=__version__)
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="ppt-agent",
            version=__version__,
        )

    app.include_router(router)
    return app


app = create_app()

