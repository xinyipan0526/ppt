# Complete Backend PPT Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a runnable first-release backend that turns approved plans into validated page images and PNG/JPG/ZIP/PDF/PPTX exports with real or deterministic Providers.

**Architecture:** Extend the existing durable workflow, Operation/Attempt, budget, Artifact, event, and recovery protocols. Keep cloud adapters, deterministic rendering, production orchestration, editing, export, HTTP, and process configuration behind focused interfaces, while retaining a single SQLite-backed process and explicit test Provider mode.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, httpx, Pydantic 2, Pillow, Playwright/Chromium, python-pptx, pypdf, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-complete-backend-ppt-agent-design.md`

## Global Constraints

- Develop on the current `feat/ppt` branch without a worktree.
- Keep the package under `src/ppt_agent` and preserve all existing public planning/material APIs.
- Use a fixed 1920 x 1080 logical canvas and immutable versioned contracts and Artifacts.
- Real Provider failures never fall back to deterministic output.
- Persist submission intent before network I/O; never automatically retry `outcome_unknown` work.
- Perform an atomic budget reservation before every potentially billable request.
- Never execute model-authored HTML, CSS, JavaScript, paths, or URLs.
- React, element-editable PPTX, arbitrary masks, local vision models, and distributed workers remain out of scope.
- Merge adjacent implementation work into milestone commits, then run a whole-batch review and full verification.

## Review Focus

- A Provider returns a redirect or private-network result URL: reject it without downloading or leaking the URL; Task 3 tests this.
- A process dies after a paid response but before Artifact completion: resume from the stored result without a second submission; Tasks 3, 4, and 7 test this.
- Chinese text cannot fit above its minimum font size: fail with `layout_overflow`, never truncate delivery-critical text; Task 5 tests this.
- One page changes while an export is being created: transactional dependency revalidation rejects or stales the export; Tasks 8 and 9 test this.
- Production mode has missing secrets, fonts, Chromium, or unsafe endpoints: startup/doctor reports a redacted actionable failure and never silently switches modes; Task 11 tests this.

---

## File Structure

New domain contracts live in `domain/production.py`, `domain/vision.py`, and `domain/exports.py`. Provider-neutral interfaces and cloud adapters live in `providers/image.py`, `providers/vision.py`, `providers/seedream.py`, and `providers/qwen.py`; durable runners live in `operations/image_runner.py` and `operations/vision_runner.py`. Rendering and export use focused `rendering/` and `exports/` packages. Production persistence/orchestration live in `workflows/production_store.py`, `workflows/deck_production.py`, and `workflows/page_editing.py`. HTTP routers remain split by resource under `api/`. Configuration and native commands live in `config/` and `cli.py`.

### Task 1: Production Contracts and Durable Schema

**Files:**
- Create: `src/ppt_agent/domain/production.py`
- Create: `src/ppt_agent/domain/vision.py`
- Create: `src/ppt_agent/domain/exports.py`
- Create: `migrations/versions/0010_production_backend.py`
- Modify: `src/ppt_agent/storage/models.py`
- Modify: `src/ppt_agent/domain/__init__.py`
- Test: `tests/unit/domain/test_production_contracts.py`
- Test: `tests/integration/storage/test_production_schema.py`

**Interfaces:**
- Produces: strict `ProductionSpecV1`, `ProductionPageSpecV1`, `VisualRequestV1`, `RenderManifestV1`, `DOMLayoutReportV1`, `VisionAnalysisV1`, `OCRReportV1`, `QualityReportV1`, `PageEditRequestV1`, `PageEditPlanV1`, and `ExportManifestV1`.
- Produces: ORM records for Provider results, production inputs, page generations, quality reports, exports, edits, material attempts, and retry schedules.

- [ ] Write contract tests for canvas invariants, safe areas, continuous page indexes, bounded blocks, normalized boxes, edit discrimination, and ordered export pages; run them and confirm failure.
- [ ] Implement the strict contract modules and exports; rerun the contract tests.
- [ ] Write migration tests that upgrade from `0009_source_materials`, enforce project-consistent Artifact references, preserve complete material attempts, and reject invalid production/export relationships; confirm failure.
- [ ] Add migration `0010_production_backend.py` and matching ORM records with unique recovery keys and composite foreign keys; rerun storage tests.
- [ ] Run domain and migration suites, then commit `feat: add production backend contracts`.

### Task 2: Provider Protocols and Deterministic Implementations

**Files:**
- Create: `src/ppt_agent/providers/image.py`
- Create: `src/ppt_agent/providers/vision.py`
- Create: `src/ppt_agent/providers/deterministic.py`
- Modify: `src/ppt_agent/providers/capabilities.py`
- Modify: `src/ppt_agent/providers/__init__.py`
- Test: `tests/unit/providers/test_image_protocol.py`
- Test: `tests/unit/providers/test_vision_protocol.py`
- Test: `tests/unit/providers/test_deterministic.py`

**Interfaces:**
- Produces: `ImageGenerationRequest/Result`, `VisionRequest/Result`, `OCRRequest/Result`, and Provider error/usage types.
- Produces: `DeterministicImageProvider`, `DeterministicVisionProvider`, and `DeterministicOCRProvider` whose results are stable by request fingerprint.

- [ ] Write failing tests for capability negotiation, input limits, request fingerprint stability, valid deterministic PNG/SVG output, normalized vision JSON, OCR spans, and predictable Usage.
- [ ] Implement Provider-neutral request/result protocols and extend capability validation without hard-coded model semantics.
- [ ] Implement deterministic Providers that generate safe fixed assets and structured results while never reading secrets or using the network.
- [ ] Run Provider unit tests, then commit `feat: define image and vision provider protocols`.

### Task 3: Seedream Adapter and Durable Image Operations

**Files:**
- Create: `src/ppt_agent/providers/seedream.py`
- Create: `src/ppt_agent/providers/result_download.py`
- Create: `src/ppt_agent/operations/image_runner.py`
- Modify: `src/ppt_agent/operations/provider.py`
- Modify: `src/ppt_agent/storage/models.py`
- Test: `tests/contract/providers/test_seedream.py`
- Test: `tests/integration/providers/test_image_runner.py`

**Interfaces:**
- Consumes: Task 2 image protocol and existing `ProviderOperationService`.
- Produces: `SeedreamProvider.generate(request)` and `ImageOperationRunner.execute(...) -> ImageOperationResult` with recoverable stored binary-result metadata.

- [ ] Write contract tests for official Ark request shape, generation/edit reference images, single-image mode, watermark/search disabling, status/error mapping, malformed success, and Usage parsing; confirm failure.
- [ ] Implement the Seedream HTTP adapter with one submission per call and safe response summaries.
- [ ] Write failing downloader tests for HTTPS/allowlist enforcement, private IPs, redirects, DNS rebinding defense at connection time, media sniffing, byte/pixel limits, and redaction.
- [ ] Implement bounded result download and Base64 decoding without accepting caller-controlled paths.
- [ ] Write failing integration tests for budget reservation, stored success-before-Artifact, crash recovery, known failure, and `outcome_unknown` behavior.
- [ ] Extend durable Provider result persistence and implement `ImageOperationRunner`; rerun Provider tests.
- [ ] Commit `feat: add durable Seedream image operations`.

### Task 4: Qwen Vision and OCR Operations

**Files:**
- Create: `src/ppt_agent/providers/qwen.py`
- Create: `src/ppt_agent/operations/vision_runner.py`
- Test: `tests/contract/providers/test_qwen.py`
- Test: `tests/integration/providers/test_vision_runner.py`

**Interfaces:**
- Consumes: Task 2 vision/OCR protocols and durable structured Provider result support.
- Produces: `QwenVisionProvider.analyze`, `QwenOCRProvider.recognize`, and `VisionOperationRunner` methods returning locally validated contracts.

- [ ] Write failing contract tests for OpenAI-compatible multimodal request bodies, JSON schema mode, bounded Base64 images, region/workspace endpoints, OCR positional normalization, Usage, and safe error mapping.
- [ ] Implement Qwen vision and OCR adapters using `httpx` without the vendor SDK.
- [ ] Write failing operation tests for atomic result persistence, schema rejection after billed success, replay after crash, missing Usage, and unknown network outcomes.
- [ ] Implement `VisionOperationRunner` and result recovery through the existing Operation/Attempt protocol.
- [ ] Run Qwen and Provider operation suites, then commit `feat: add durable Qwen vision operations`.

### Task 5: Deterministic Layout, Fonts, Chromium Rendering, and Local Validation

**Files:**
- Create: `src/ppt_agent/rendering/__init__.py`
- Create: `src/ppt_agent/rendering/layout.py`
- Create: `src/ppt_agent/rendering/fonts.py`
- Create: `src/ppt_agent/rendering/templates.py`
- Create: `src/ppt_agent/rendering/chromium.py`
- Create: `src/ppt_agent/rendering/validation.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/rendering/test_layout.py`
- Test: `tests/unit/rendering/test_fonts.py`
- Test: `tests/unit/rendering/test_validation.py`
- Test: `tests/integration/rendering/test_chromium.py`

**Interfaces:**
- Produces: `compile_production_spec(...)`, `FontRegistry.scan()`, `ChromiumRenderer.render(manifest)`, and `validate_page(...)`.
- Produces: PNG bytes plus `DOMLayoutReportV1` and local `QualityReportV1`.

- [ ] Write failing layout tests for deterministic Planning-to-Production compilation, safe-area placement, supported tokens, natural wrapping, fallback layouts, and non-truncating `layout_overflow`.
- [ ] Implement the versioned layout compiler and repository-owned HTML/CSS/SVG templates.
- [ ] Write failing font tests for approved fallback selection, content hashes, missing Chinese fonts, and cache identity.
- [ ] Implement `FontRegistry` without silent random system fallback.
- [ ] Write failing Chromium tests for fixed viewport, disabled network/script access, isolated temporary files, font readiness, timeout, DOM report, and child cleanup.
- [ ] Add Playwright and implement `ChromiumRenderer`; allow injection of a deterministic renderer in tests where Chromium is unavailable.
- [ ] Write and implement local checks for bounds, overlap, size, contrast, blank pages, page numbers, and Artifact integrity.
- [ ] Run rendering suites, then commit `feat: render and validate deterministic pages`.

### Task 6: Production Store and Page Artifact Pipeline

**Files:**
- Create: `src/ppt_agent/workflows/production_store.py`
- Create: `src/ppt_agent/workflows/artifact_pipeline.py`
- Test: `tests/integration/workflows/test_production_store.py`
- Test: `tests/integration/workflows/test_artifact_pipeline.py`

**Interfaces:**
- Produces: `ProductionStore.create_run`, `claim_page_stage`, `complete_page_stage`, `fail_page_stage`, `create_generation`, and `current_pages`.
- Produces: idempotent commit helpers for visual, manifest, DOM report, page image, thumbnail, and quality Artifacts.

- [ ] Write failing transactional tests for approved-bundle revalidation, project ownership, unique generations, stage compare-and-swap, cancellation, and concurrent claims.
- [ ] Implement `ProductionStore` using short `BEGIN IMMEDIATE` write sections and existing formal states.
- [ ] Write failing crash-boundary tests for every Artifact type and dependency edge, including replay after file commit and before step commit.
- [ ] Implement the page Artifact pipeline with stable logical IDs, operation IDs, fingerprints, and dependency edges.
- [ ] Run workflow-store suites, then commit `feat: persist production page pipeline`.

### Task 7: Deck Production Workflow and Recovery

**Files:**
- Create: `src/ppt_agent/workflows/deck_production.py`
- Modify: `src/ppt_agent/runtime/recovery.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `src/ppt_agent/runtime/supervisor.py`
- Test: `tests/integration/workflows/test_deck_production.py`
- Test: `tests/integration/runtime/test_production_recovery.py`

**Interfaces:**
- Consumes: Tasks 3-6 adapters, runners, renderer, validator, store, and pipeline.
- Produces: `DeckProductionWorkflow.execute(run_id)` and runtime composition for planning and production run types.

- [ ] Write failing end-to-end workflow tests for deterministic, hybrid, and full-raster pages; global/project/Provider limits; budget waits; cancellation; and partial-page failure.
- [ ] Implement the deterministic production stage machine and persist core events at every formal state boundary.
- [ ] Write failing recovery tests for stored Provider success, committed visual, committed page image, interrupted validation, unknown outcomes, and completed pages awaiting export.
- [ ] Extend recovery and runtime dispatch so only proven-safe local stages automatically resume.
- [ ] Run workflow and runtime suites, then commit `feat: run recoverable deck production`.

### Task 8: Page Regeneration and Natural-Language Editing

**Files:**
- Create: `src/ppt_agent/workflows/page_editing.py`
- Create: `src/ppt_agent/tools/page_editing.py`
- Test: `tests/unit/tools/test_page_editing.py`
- Test: `tests/integration/workflows/test_page_editing.py`

**Interfaces:**
- Produces: `PageEditingWorkflow.request_edit(...)`, `resume_edit(...)`, and `request_regeneration(...)`.
- Produces: deterministic edit planner validation and Qwen target-location compilation.

- [ ] Write failing tests for content/style/visual/full-page/ambiguous classification and rejection of arbitrary executable fields or masks.
- [ ] Implement strict edit-plan compilation with a versioned clarification Interrupt for ambiguity.
- [ ] Write failing workflow tests proving content/style edits never call Seedream, visual edits use Qwen then Seedream, resampling is explicit, history is immutable, and only target-page/export dependencies become stale.
- [ ] Implement edit and regeneration workflows with revision lineage and idempotent recovery keys.
- [ ] Run edit suites, then commit `feat: regenerate and edit individual pages`.

### Task 9: Export Pipeline

**Files:**
- Create: `src/ppt_agent/exports/__init__.py`
- Create: `src/ppt_agent/exports/images.py`
- Create: `src/ppt_agent/exports/zip.py`
- Create: `src/ppt_agent/exports/pdf.py`
- Create: `src/ppt_agent/exports/pptx.py`
- Create: `src/ppt_agent/workflows/exporting.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/exports/test_exporters.py`
- Test: `tests/integration/workflows/test_exporting.py`

**Interfaces:**
- Produces: pure exporter functions consuming ordered page bytes and `ExportWorkflow.create/export/status`.
- Produces: available export Artifacts backed by `ExportManifestV1`.

- [ ] Write failing format tests for PNG/JPG dimensions, safe ZIP names/order, 16:9 PDF pages, and one full-slide image per PPTX slide.
- [ ] Add python-pptx and implement bounded exporters with deterministic metadata and no user-controlled paths.
- [ ] Write failing workflow tests for cache identity, transactional final page revalidation, stale/invalid/unknown refusal, explicit force acknowledgement, crash recovery, and export invalidation after page edits.
- [ ] Implement export persistence, Artifact commit, and dependencies.
- [ ] Run export suites, then commit `feat: export image based presentation formats`.

### Task 10: Project, Production, Edit, Export, Cost, and Recovery APIs

**Files:**
- Create: `src/ppt_agent/api/projects.py`
- Create: `src/ppt_agent/api/providers.py`
- Create: `src/ppt_agent/api/production.py`
- Create: `src/ppt_agent/api/exports.py`
- Create: `src/ppt_agent/api/operations.py`
- Modify: `src/ppt_agent/api/app.py`
- Modify: `src/ppt_agent/api/security.py`
- Test: `tests/integration/api/test_projects.py`
- Test: `tests/integration/api/test_production.py`
- Test: `tests/integration/api/test_exports.py`
- Test: `tests/integration/api/test_provider_status.py`

**Interfaces:**
- Produces: authenticated REST endpoints defined by the design and authorized streaming Artifact downloads.

- [ ] Write failing API tests for project CRUD, capability/status redaction, explicit connection tests, production lifecycle, page edits/regeneration, costs, recovery authorization, export creation/status/download, and SSE continuity.
- [ ] Implement routers with stable response/error models, `InputLimits`, resource authorization, and no direct filesystem path inputs.
- [ ] Write failing race/security tests for cross-project IDs, stale planning bundles, concurrent page edit/export, range/stream abort behavior, malicious filenames, Host/Origin, and secret/URL/error redaction.
- [ ] Harden API transactions and streaming; rerun API suites.
- [ ] Commit `feat: expose complete backend workflow api`.

### Task 11: Configuration, Native CLI, Diagnostics, and Runtime Composition

**Files:**
- Create: `src/ppt_agent/config/__init__.py`
- Create: `src/ppt_agent/config/settings.py`
- Create: `src/ppt_agent/config/secrets.py`
- Create: `src/ppt_agent/cli.py`
- Create: `config/ppt-agent.example.toml`
- Create: `.env.example`
- Create: `Dockerfile`
- Create: `compose.yaml`
- Modify: `pyproject.toml`
- Modify: `src/ppt_agent/runtime/application.py`
- Test: `tests/unit/config/test_settings.py`
- Test: `tests/integration/cli/test_cli.py`
- Test: `tests/integration/runtime/test_full_application.py`

**Interfaces:**
- Produces: `load_settings(...)`, `build_application(settings)`, and `ppt-agent serve|doctor|migrate`.

- [ ] Write failing tests for CLI > environment > TOML > defaults precedence, secret indirection, redaction, Provider-mode validation, HTTPS production endpoints, and deterministic no-key configuration.
- [ ] Implement typed settings with `tomllib`, environment mapping, keyring/secret-file loaders, and a redacted effective view.
- [ ] Write failing CLI tests for migrations, diagnostics, native serving composition, missing Chromium/fonts/keys, and no import-time side effects.
- [ ] Implement argparse-based commands, complete runtime/router composition, and actionable redacted diagnostics.
- [ ] Add a backend container and Compose example using secret files and a persistent Workspace.
- [ ] Run configuration, CLI, and full-runtime suites, then commit `feat: ship runnable backend service`.

### Task 12: Infrastructure Debt, Documentation, Live Tests, and Whole-System Verification

**Files:**
- Modify: `src/ppt_agent/artifacts/store.py`
- Modify: `src/ppt_agent/materials/store.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/runtime/maintenance.py`
- Create: `tests/live/providers/test_seedream_live.py`
- Create: `tests/live/providers/test_qwen_live.py`
- Create: `tests/e2e/test_deterministic_deck.py`
- Create: `docs/quickstart.md`
- Create: `docs/configuration.md`
- Create: `docs/provider-setup.md`
- Create: `docs/api-tutorial.md`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: a reproducible no-key end-to-end service, opt-in billable smoke tests, complete operator documentation, and an accurate handoff checkpoint.

- [ ] Add regression tests and fixes for Workspace maintenance locking, Material attempt history, deterministic intent sufficiency, and persisted due-time retries for known failures only.
- [ ] Write the deterministic API-level input-to-PPTX test and verify page count, formats, dependency graph, costs, events, restart, and download.
- [ ] Add opt-in Seedream generation/edit and Qwen vision/OCR live tests guarded by explicit switches, complete credentials, and conservative call limits.
- [ ] Write native and container quickstarts, full configuration reference, Provider account/region/model setup, API tutorial, recovery/unknown-result guidance, and cost warnings; update README and AGENTS accurately.
- [ ] Run all focused suites and then `pytest -q --basetemp=.cache/pytest-complete-backend`; require all non-environmental tests to pass.
- [ ] Run `python -m compileall -q src tests migrations`, `git diff --check`, and inspect `git status --short`.
- [ ] Request one independent whole-branch review against the spec; fix every Critical and Important finding with regression tests.
- [ ] Repeat the full suite, compileall, diff check, and status check, then commit `docs: complete backend ppt agent handoff`.
