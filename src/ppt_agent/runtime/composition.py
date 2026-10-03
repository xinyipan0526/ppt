"""Native composition root for the complete backend service."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from ppt_agent.api.app import create_app
from ppt_agent.api.operations import operations_router
from ppt_agent.api.production import production_router
from ppt_agent.api.projects import projects_router
from ppt_agent.api.providers import providers_router
from ppt_agent.api.security import AccessTokenManager, InputLimits, LocalHTTPConfig, LocalHTTPSecurity, SecretFileTokenStore
from ppt_agent.api.settings import provider_settings_router
from ppt_agent.api.workspace import workspace_router
from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.secrets import WorkspaceTokenStore
from ppt_agent.config.settings import Settings
from ppt_agent.runtime.application import ApplicationRuntime, ApplicationRuntimeConfig
from ppt_agent.runtime.backend import (
    BackendWorkflowDispatcher,
    build_material_image_analyzer,
    build_material_runtime,
)
from ppt_agent.runtime.provider_config import ProviderConfigurationService
from ppt_agent.providers.registry import AdapterRegistry
from ppt_agent.workflows.deck_generations import DeckGenerationService
from ppt_agent.web import mount_web_app
from ppt_agent.storage.database import session_scope
from ppt_agent.storage.models import ProjectRecord, WorkflowRunRecord


class _Lazy:
    def __init__(self, resolve: Callable[[], Any]) -> None:
        object.__setattr__(self, "_resolve", resolve)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_resolve")(), name)

    def __call__(self, *args, **kwargs):
        return object.__getattribute__(self, "_resolve")()(*args, **kwargs)


def build_application(settings: Settings):
    settings.workspace.mkdir(parents=True, exist_ok=True)
    if settings.local_access_token:
        token = settings.local_access_token
    else:
        store = SecretFileTokenStore(settings.local_access_token_file) if settings.local_access_token_file else WorkspaceTokenStore(settings.workspace)
        token = AccessTokenManager(store).load_or_create()
    authorities = {
        f"localhost:{settings.port}", f"127.0.0.1:{settings.port}",
        f"[::1]:{settings.port}",
    }
    if settings.host not in {"0.0.0.0", "::"}:
        authorities.add(f"{settings.host}:{settings.port}")
    origins = {f"http://{authority}" for authority in authorities}
    if not settings.production:
        origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    security = LocalHTTPSecurity(
        token=token,
        config=LocalHTTPConfig(
            allowed_authorities=frozenset(authorities),
            allowed_origins=frozenset(origins),
        ),
    )
    limits = InputLimits()
    runtime_ref: dict[str, ApplicationRuntime] = {}

    def is_idle() -> bool:
        current = runtime_ref.get("runtime")
        if current is None or current.supervisor is None:
            return True
        runs_idle = not current.supervisor.active_run_ids
        materials_idle = (
            current.material_runtime is None
            or not current.material_runtime.supervisor.active_material_ids  # type: ignore[attr-defined]
        )
        return runs_idle and materials_idle

    def activate(candidate: Settings) -> None:
        current = runtime_ref.get("runtime")
        if current is None or not current.ready:
            return
        value = current.planning_workflow
        if not isinstance(value, BackendWorkflowDispatcher) or current.sessions is None:
            raise RuntimeError("backend workflows are not ready")
        bundle = AdapterRegistry(candidate.ark_api_key).build(
            provider_configuration.snapshot()
        )
        value.reconfigure(candidate, bundle)
        if current.material_runtime is not None:
            current.material_runtime.service.image_analyzer = build_material_image_analyzer(  # type: ignore[attr-defined]
                current.sessions, candidate, bundle
            )

    provider_configuration = ProviderConfigurationService(
        settings,
        FileProviderSecretStore(settings.workspace / ".secrets" / "adapters"),
        is_idle=is_idle,
        activate=activate,
        settings_path=settings.workspace / "config" / "provider-settings.json",
    )

    def workflow_factory(sessions, artifacts, repository):
        bundle = AdapterRegistry(provider_configuration.settings.ark_api_key).build(
            provider_configuration.snapshot()
        )
        return BackendWorkflowDispatcher(
            sessions,
            artifacts,
            repository,
            provider_configuration.settings,
            bundle,
        )

    def material_factory(sessions, artifacts):
        bundle = AdapterRegistry(provider_configuration.settings.ark_api_key).build(
            provider_configuration.snapshot()
        )
        return build_material_runtime(
            sessions, artifacts, workspace=settings.workspace, limits=limits,
            shutdown_timeout=10.0,
            settings=provider_configuration.settings,
            provider_bundle=bundle,
        )

    runtime: ApplicationRuntime

    async def cancel_run(run_id: str) -> None:
        value = runtime.planning_workflow
        if not isinstance(value, BackendWorkflowDispatcher):
            raise RuntimeError("backend workflows are not ready")
        await value.cancel(run_id)

    runtime = ApplicationRuntime(
        ApplicationRuntimeConfig(
            workspace=settings.workspace, production=settings.production,
            minimum_free_bytes=10 * 1024 * 1024,
            global_limit=settings.global_concurrency,
            project_limit=settings.project_concurrency,
        ),
        planning_workflow_factory=workflow_factory,
        material_runtime_factory=material_factory,
        cancellation_executor=cancel_run,
    )
    runtime_ref["runtime"] = runtime

    def sessions():
        if runtime.sessions is None:
            raise RuntimeError("application runtime is not ready")
        return runtime.sessions

    def dispatcher() -> BackendWorkflowDispatcher:
        value = runtime.planning_workflow
        if not isinstance(value, BackendWorkflowDispatcher):
            raise RuntimeError("backend workflows are not ready")
        return value

    def material_service():
        if runtime.material_runtime is None:
            raise RuntimeError("material runtime is not ready")
        return runtime.material_runtime.service  # type: ignore[attr-defined]

    async def authorize_project(project_id: str) -> None:
        def check() -> bool:
            with session_scope(sessions()) as session:
                return session.get(ProjectRecord, project_id) is not None
        if not await asyncio.to_thread(check):
            raise HTTPException(status_code=404, detail="project not found")

    async def authorize_run(run_id: str) -> None:
        def check() -> bool:
            with session_scope(sessions()) as session:
                return session.get(WorkflowRunRecord, run_id) is not None
        if not await asyncio.to_thread(check):
            raise HTTPException(status_code=404, detail="run not found")

    app = create_app(
        runtime=runtime, security=security,
        authorize_run=authorize_run, authorize_project=authorize_project,
        planning_workflow=_Lazy(lambda: dispatcher().planning),
        planning_supervisor=_Lazy(lambda: runtime.supervisor),
        material_service=_Lazy(material_service),
        material_supervisor=_Lazy(lambda: runtime.material_runtime.supervisor),
        input_limits=limits,
    )
    session_proxy = _Lazy(sessions)
    deck_generations = _Lazy(lambda: DeckGenerationService(sessions()))
    app.include_router(projects_router(sessions=session_proxy, security=security))
    app.include_router(providers_router(
        resolve_settings=lambda: provider_configuration.settings,
        security=security,
        connection_tests=_connection_tests(lambda: provider_configuration.settings),
    ))
    app.include_router(provider_settings_router(
        service=provider_configuration, security=security,
    ))
    app.include_router(production_router(
        coordinator=_Lazy(lambda: dispatcher().production_coordinator),
        store=_Lazy(lambda: dispatcher().production_store),
        supervisor=_Lazy(lambda: runtime.supervisor),
        editing=_Lazy(lambda: dispatcher().editing),
        exporting=_Lazy(lambda: dispatcher().exporting),
        artifacts=_Lazy(lambda: runtime.artifacts),
        authorize_project=authorize_project, authorize_run=authorize_run,
        security=security,
        deck_generations=deck_generations,
    ))
    app.include_router(workspace_router(
        sessions=session_proxy,
        artifacts=_Lazy(lambda: runtime.artifacts),
        decks=deck_generations,
        editing=_Lazy(lambda: dispatcher().editing),
        authorize_project=authorize_project,
        authorize_run=authorize_run,
        security=security,
    ))
    app.include_router(operations_router(
        sessions=session_proxy,
        operations=_Lazy(lambda: dispatcher().operations),
        authorize_project=authorize_project, authorize_run=authorize_run,
        security=security,
        repository=_Lazy(lambda: runtime.repository),
        supervisor=_Lazy(lambda: runtime.supervisor),
    ))
    app.state.settings = settings.redacted()
    app.state.provider_configuration = provider_configuration
    app.state.runtime = runtime
    app.state.access_token = token
    mount_web_app(app)
    return app


def _connection_tests(resolve_settings: Callable[[], Settings]) -> dict[str, Callable]:
    def callback(provider: str):
        async def test() -> dict[str, object]:
            settings = resolve_settings()
            if settings.provider_mode != "cloud":
                return {"mode": "deterministic"}
            base_url = getattr(settings, f"{provider}_base_url")
            api_key = getattr(settings, f"{provider}_api_key") or ""
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers={"authorization": f"Bearer {api_key}"},
                )
            response.raise_for_status()
            return {"http_status": response.status_code}
        return test

    return {
        "deepseek": callback("deepseek"),
        "seedream": callback("seedream"),
        "qwen": callback("qwen"),
    }
