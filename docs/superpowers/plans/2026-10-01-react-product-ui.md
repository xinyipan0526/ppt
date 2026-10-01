# React Product UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a same-origin React product UI that securely configures cloud providers and operates the complete durable PPT workflow from project creation through editing, global regeneration, recovery, and export.

**Architecture:** FastAPI serves a packaged Vite SPA and narrow authenticated UI projections. Browser access uses an HttpOnly same-origin session plus CSRF while bearer authentication remains available to automation. React organizes server state by feature with TanStack Query and renders the persisted workflow rather than reproducing backend state machines.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, React, TypeScript, Vite, React Router, TanStack Query, React Hook Form, Zod, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-01-react-product-ui-design.md`

## Global Constraints

- Work on the current `feat/ppt` branch; do not create a worktree.
- Preserve deterministic no-key operation and all provider cost/recovery semantics.
- Never persist or return provider secrets through browser storage, database, logs, events, diagnostics, URLs, or API responses.
- Serve production UI and API from one origin; packaged users do not need Node.js.
- Keep image-only PPTX scope; do not introduce element-level editing or phase-two canvas masking.
- Use immutable page and deck generations; restore creates a new generation.
- Keep source files focused and avoid a general frontend global-state store.
- Chinese is the default interface language and desktop authoring is the primary layout.

## Review Focus

- A malicious cross-origin page targeting localhost must not bootstrap a usable authenticated mutation session; covered by Task 1 transport and CSRF tests.
- A failed secret-store write must not replace the active provider configuration or leave a partial secret; covered by Task 1 atomicity tests.
- Refreshing during clarification or global regeneration must reconstruct the same actionable state; covered by Tasks 2 and 6 integration tests.
- A project must never read another project's page image or generation history; covered by Task 2 authorization tests.
- Partial production failure must retain successful page previews and retry only the failed scope; covered by Task 2 backend and Task 6 UI tests.

---

### Task 1: Secure browser session and runtime provider configuration

**Files:**
- Create: `src/ppt_agent/config/provider_secrets.py`
- Create: `src/ppt_agent/runtime/provider_config.py`
- Create: `src/ppt_agent/api/settings.py`
- Modify: `src/ppt_agent/api/security.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Modify: `src/ppt_agent/config/settings.py`
- Test: `tests/unit/config/test_provider_secrets.py`
- Test: `tests/integration/api/test_browser_session.py`
- Test: `tests/integration/api/test_settings.py`

**Interfaces:**
- Produces: `ProviderSecretStore.status()`, `.replace(name, value)`, `.delete(name)`; `ProviderConfigurationService.snapshot()`, `.update(request)`, `.activate_if_idle()`; `/api/v1/session`, `/api/v1/settings/providers`, and `/api/v1/settings/providers/test`.
- Preserves: bearer-token authentication for CLI/API clients and `LocalHTTPSecurity` Host/Origin guarantees.

- [ ] Write failing tests for atomic secret writes, masked status, response/log non-disclosure, cookie bootstrap, SameSite/HttpOnly flags, CSRF rejection, cross-origin rejection, and deferred activation with an active run.
- [ ] Run the focused tests and confirm failures are due to missing interfaces.
- [ ] Implement the provider secret store, configuration service, dual bearer/cookie authentication, CSRF validation, and settings routes.
- [ ] Refactor provider construction behind an atomically swappable configuration snapshot without changing a run after it starts.
- [ ] Run Task 1 tests and existing security/provider suites.
- [ ] Commit the secure configuration milestone.

### Task 2: UI projections, page delivery, and immutable deck generations

**Files:**
- Create: `src/ppt_agent/api/workspace.py`
- Create: `src/ppt_agent/workflows/deck_generations.py`
- Modify: `src/ppt_agent/api/projects.py`
- Modify: `src/ppt_agent/api/planning.py`
- Modify: `src/ppt_agent/api/production.py`
- Modify: `src/ppt_agent/workflows/production_store.py`
- Modify: `src/ppt_agent/workflows/production_service.py`
- Modify: `src/ppt_agent/storage/models.py`
- Create: `migrations/versions/0012_deck_generations.py`
- Test: `tests/integration/api/test_workspace.py`
- Test: `tests/integration/workflows/test_deck_generations.py`
- Test: `tests/integration/storage/test_deck_generation_schema.py`

**Interfaces:**
- Consumes: authenticated browser/bearer identity from Task 1.
- Produces: project workspace summaries; safe plan detail; page image/thumbnail/quality/spec/history responses; page-spec revision/restore; deck-generation create/regenerate/history/restore.

- [ ] Write failing migration, ownership, version-conflict, projection-shape, refresh-recovery, partial-failure, and cross-project artifact tests.
- [ ] Run the focused tests and verify the intended failures.
- [ ] Add immutable deck-generation persistence and transactional services that reuse current page generation and artifact dependency rules.
- [ ] Add narrow workspace/read-model and command routes; stream authorized images without exposing filesystem paths or general Artifact reads.
- [ ] Run Task 2 tests plus planning, production, editing, export, migration, and security regression suites.
- [ ] Commit the UI backend-contract milestone.

### Task 3: React toolchain, typed client, and product shell

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/src/main.tsx`
- Create: `web/src/app/App.tsx`
- Create: `web/src/app/router.tsx`
- Create: `web/src/api/client.ts`
- Create: `web/src/api/contracts.ts`
- Create: `web/src/api/events.ts`
- Create: `web/src/styles/tokens.css`
- Create: `web/src/styles/global.css`
- Create: `web/src/test/setup.ts`
- Test: `web/src/api/client.test.ts`
- Test: `web/src/app/App.test.tsx`

**Interfaces:**
- Consumes: Task 1 session/CSRF routes and Task 2 projections.
- Produces: `apiClient`, resumable `RunEventStream`, routed `AppShell`, reusable query/error boundaries, and the shared dark workspace visual system.

- [ ] Add the locked frontend dependencies and strict TypeScript/Vitest/Vite configuration.
- [ ] Write failing client tests for credentials, CSRF, 401 bootstrap, normalized errors, cursor continuation, and bounded polling fallback.
- [ ] Implement the client, event stream, query provider, router, shell, responsive rails, accessible overlays, and Chinese copy foundation.
- [ ] Run frontend typecheck and focused unit tests.
- [ ] Commit the runnable React shell.

### Task 4: Onboarding, provider settings, projects, and materials

**Files:**
- Create: `web/src/features/settings/ProviderSetup.tsx`
- Create: `web/src/features/settings/providerSchemas.ts`
- Create: `web/src/features/projects/ProjectList.tsx`
- Create: `web/src/features/projects/ProjectWorkspace.tsx`
- Create: `web/src/features/materials/MaterialPanel.tsx`
- Create: `web/src/features/materials/UploadDropzone.tsx`
- Test: `web/src/features/settings/ProviderSetup.test.tsx`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`
- Test: `web/src/features/materials/MaterialPanel.test.tsx`

**Interfaces:**
- Consumes: Task 3 API client/shell and settings/project/material APIs.
- Produces: first-run setup, redacted provider health, project CRUD navigation, streamed uploads, parse retry/delete, and ready-material selection.

- [ ] Write failing tests for secret clearing, absence from browser storage, masked status, deterministic-mode continuation, project creation, upload progress, ready-only selection, and parse retry.
- [ ] Implement onboarding/settings and project/material workspace features with accessible loading, empty, error, and conflict states.
- [ ] Run Task 4 frontend tests and typecheck.
- [ ] Commit the onboarding and source-input milestone.

### Task 5: Clarification and planning review workspace

**Files:**
- Create: `web/src/features/planning/PlanningInput.tsx`
- Create: `web/src/features/planning/ClarificationForm.tsx`
- Create: `web/src/features/planning/PlanReview.tsx`
- Create: `web/src/features/planning/PageSpecCard.tsx`
- Create: `web/src/features/planning/planningSchemas.ts`
- Test: `web/src/features/planning/ClarificationForm.test.tsx`
- Test: `web/src/features/planning/PlanReview.test.tsx`

**Interfaces:**
- Consumes: planning creation, snapshot/SSE, clarification resume, and safe plan-detail projection.
- Produces: resumable planning UI, schema-driven clarification, Brief/Outline/DesignSystem/PageSpec review, approval, cancellation, whole-plan revision, and selected-page revision.

- [ ] Write failing tests for persisted-interrupt rendering, schema validation, optimistic conflict refresh, page selection, and approval gating.
- [ ] Implement planning input, event-driven progress, clarification forms, and the complete review surface.
- [ ] Run Task 5 tests, typecheck, and focused backend planning tests.
- [ ] Commit the planning workspace milestone.

### Task 6: Slide gallery, per-page editing, global regeneration, recovery, and export

**Files:**
- Create: `web/src/features/deck/DeckGallery.tsx`
- Create: `web/src/features/deck/SlideViewer.tsx`
- Create: `web/src/features/deck/PageInspector.tsx`
- Create: `web/src/features/deck/PageHistory.tsx`
- Create: `web/src/features/deck/GlobalRegenerateDialog.tsx`
- Create: `web/src/features/runs/RunStatus.tsx`
- Create: `web/src/features/runs/RecoveryActions.tsx`
- Create: `web/src/features/exports/ExportPanel.tsx`
- Test: `web/src/features/deck/DeckGallery.test.tsx`
- Test: `web/src/features/deck/PageInspector.test.tsx`
- Test: `web/src/features/deck/GlobalRegenerateDialog.test.tsx`
- Test: `web/src/features/runs/RecoveryActions.test.tsx`
- Test: `web/src/features/exports/ExportPanel.test.tsx`

**Interfaces:**
- Consumes: Task 2 page/deck generation APIs, existing edit/recovery/export APIs, and Task 3 event client.
- Produces: full-deck primary view, focused preview, independent text/design/edit entries, immutable history/restore, guarded global regeneration, accurate recovery controls, and downloadable exports.

- [ ] Write failing tests for successful-page retention under partial failure, focused/gallery navigation, edit clarification, page restore, global-regeneration confirmation contents, refresh reconstruction, unknown-outcome safeguards, and PPTX-primary export.
- [ ] Implement the gallery, viewer, inspector, page editing/history, global generation, run/recovery, cost, and export experiences.
- [ ] Run Task 6 tests, full frontend unit tests, typecheck, and focused backend workflow/API tests.
- [ ] Commit the complete authoring workspace milestone.

### Task 7: Static delivery, packaging, CLI, Docker, and documentation

**Files:**
- Create: `src/ppt_agent/web.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `src/ppt_agent/cli.py`
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/configuration.md`
- Modify: `docs/provider-setup.md`
- Create: `docs/ui-guide.md`
- Test: `tests/integration/api/test_web_app.py`
- Test: `tests/integration/cli/test_cli.py`

**Interfaces:**
- Consumes: the Task 3–6 Vite production build.
- Produces: package-owned static assets, SPA fallback, `ppt-agent serve --no-open`, browser launch, Docker build, and end-user product documentation.

- [ ] Write failing tests for root delivery, immutable asset caching, API 404 preservation, SPA history fallback, missing-assets diagnostics, and CLI browser opt-out.
- [ ] Implement static serving and package-data inclusion; update CLI, image build, Compose defaults, and user documentation.
- [ ] Build the production frontend, build/install the Python wheel, and smoke-test both native and container entrypoints without cloud billing.
- [ ] Commit the distributable product milestone.

### Task 8: Deterministic browser E2E, security audit, and release verification

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/product-flow.spec.ts`
- Create: `web/e2e/secret-safety.spec.ts`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the complete same-origin product.
- Produces: deterministic browser acceptance coverage and an updated repository handoff.

- [ ] Add Playwright coverage for setup, project/material input, clarification, plan approval, eight-page production, page edit, global regeneration, restart reconstruction, and PPTX download.
- [ ] Add browser security assertions that secrets never appear in storage, URL/history, DOM after submit, API responses, events, or captured console output.
- [ ] Run all frontend tests, typecheck, build, Playwright, full Python tests, and `compileall`.
- [ ] Run `git diff --check`, inspect the complete branch diff for security and workflow regressions, and fix Important or Critical findings with regression tests.
- [ ] Update `AGENTS.md` with exact verification counts, delivered behavior, commit checkpoints, and remaining frontend limitations.
- [ ] Commit the final verified product handoff.
