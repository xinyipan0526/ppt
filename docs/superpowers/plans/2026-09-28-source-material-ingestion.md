# Source Material Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, authenticated Markdown/PDF/image upload and parsing path whose validated outputs can be consumed safely by the existing planning workflow.

**Architecture:** Commit original bytes through `ArtifactStore` first, persist a `SourceMaterial` state machine, and perform risky decoding in bounded spawned child processes. A material runtime reconciles interrupted parses and exposes immutable `ParsedMaterialV1` Artifacts; planning accepts material IDs and resolves only ready, same-project parsed Artifacts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, SQLite, Pillow, pypdf, multiprocessing `spawn`, pytest

**Spec:** `docs/superpowers/specs/2026-09-28-source-material-ingestion-design.md`

## Global Constraints

- Work on the current `feat/ppt` branch; do not create a worktree.
- Preserve the single-process modular-monolith architecture; do not add Redis, Celery, a standalone worker, leases, or heartbeat.
- Accept only UTF-8 Markdown, PDF, PNG, JPEG, and WebP in this milestone.
- Commit original bytes before parsing and never overwrite immutable source or parsed Artifacts.
- Treat every extracted text block as untrusted source content and never fetch external resources.
- Enforce `InputLimits`: 50 MiB per file, 500 MiB per project, 40,000,000 image pixels, one image frame, 200 PDF pages, 200,000 PDF objects, 2,000,000 Markdown characters, nesting depth 32, 100 external resources, compression ratio 100, and parser timeout 60 seconds unless explicitly configured otherwise.
- Windows guarantees process separation, isolated temporary storage, timeout, and forced termination; POSIX additionally applies available CPU/address-space limits.
- Keep OCR, remote vision, Seedream, Renderer, export, React, and element-editable PPTX out of scope.
- Complete this as one infrastructure batch with one final independent review, then run the full suite, `compileall`, and `git diff --check`.

## Review Focus

- A client disconnects midway through an oversized upload: staging is recoverable and no material becomes usable (`test_disconnected_upload_leaves_no_ready_material`, Task 3).
- Two uploads race just below the project quota: the transactional recheck permits at most one (`test_project_quota_is_atomic_under_concurrent_finalize`, Task 1).
- A parser child returns a huge or malformed IPC payload: the parent rejects it without exhausting memory or committing an Artifact (`test_worker_rejects_oversized_result_envelope`, Task 2).
- A valid file is mislabeled with a conflicting `Content-Type` and extension: signature/decoding wins and the response does not trust the label (`test_upload_uses_sniffed_type_over_claimed_type`, Task 3).
- Planning sees a ready material whose parsed Artifact became stale or invalid after the initial lookup: creation fails atomically instead of recording an unusable source (`test_planning_rechecks_artifact_availability_in_creation_boundary`, Task 4).

---

### Task 1: Material contracts, persistence, and atomic state store

**Files:**
- Create: `src/ppt_agent/domain/materials.py`
- Create: `src/ppt_agent/materials/__init__.py`
- Create: `src/ppt_agent/materials/store.py`
- Create: `migrations/versions/0009_source_materials.py`
- Modify: `src/ppt_agent/storage/models.py`
- Modify: `src/ppt_agent/domain/__init__.py`
- Test: `tests/unit/domain/test_material_contracts.py`
- Test: `tests/integration/materials/test_material_store.py`
- Test: `tests/integration/storage/test_workflow_schema.py`

**Interfaces:**
- Consumes: `SessionFactory`, `ArtifactStore.get(artifact_id) -> ArtifactSnapshot`, existing `ArtifactStatus`, `InputLimits` serialized by callers.
- Produces: `MaterialStatus`, `MaterialErrorCode`, `SourceLocatorV1`, `TextBlockV1`, `ParsedMaterialV1`; `MaterialStore.create_uploaded(...) -> MaterialSnapshot`, `claim(material_id) -> MaterialSnapshot | None`, `complete(material_id, *, expected_version, parsed_artifact_id) -> MaterialSnapshot`, `fail(...) -> MaterialSnapshot`, `retry(...) -> MaterialSnapshot`, `delete(...) -> MaterialSnapshot`, `get(...)`, `list_project(...)`, `resolve_ready(project_id, material_ids) -> tuple[ResolvedMaterial, ...]`, `reconcile() -> MaterialReconciliationReport`.

- [ ] **Step 1: Write failing contract and state-store tests**

Add tests that assert strict `ParsedMaterialV1` validation, required literal `untrusted_source_content=True`, deterministic text block IDs, allowed state transitions, version conflicts, same-project Artifact ownership, ready-state invariants, logical deletion, and the transactional cumulative-byte quota. Include a two-session concurrency test named `test_project_quota_is_atomic_under_concurrent_finalize`.

- [ ] **Step 2: Run Task 1 tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_material_contracts.py tests/integration/materials/test_material_store.py tests/integration/storage/test_workflow_schema.py -q --basetemp='.cache/pytest-materials-store-red'`

Expected: FAIL because material contracts, table, and store do not exist.

- [ ] **Step 3: Implement versioned domain contracts**

In `domain/materials.py`, define string enums and strict frozen Pydantic models. `ParsedMaterialV1` must expose `model_json_schema()`/`model_validate_json()`, reject extra fields, cap collection sizes, normalize no source text, and generate block IDs with `build_text_block_id(material_id: str, locator: SourceLocatorV1, text: str) -> str` using canonical JSON and SHA-256.

- [ ] **Step 4: Add migration and ORM model**

Create `SourceMaterialRecord` and revision `0009_source_materials` with status/error checks, non-negative sizes/attempt count, optimistic version, unique current parsed Artifact linkage, indexes for project/status, and foreign keys. Add `UniqueConstraint("id", "project_id", name="uq_artifact_id_project")` to `artifacts`, then use composite foreign keys `(source_artifact_id, project_id)` and `(parsed_artifact_id, project_id)` so the database enforces project consistency.

- [ ] **Step 5: Implement `MaterialStore` transaction boundaries**

Use `BEGIN IMMEDIATE` for upload finalization, claim, complete, retry, delete, and resolution. `create_uploaded` accepts `project_id`, sanitized display name, sniffed media type, source Artifact ID, source bytes, parser identity, limits dict/fingerprint, and optional stable material ID. It rechecks the non-deleted project byte total in the same transaction. `resolve_ready` preserves request order and verifies status plus Artifact availability inside the same transaction.

- [ ] **Step 6: Implement startup reconciliation**

`reconcile()` must attach a uniquely keyed available parsed Artifact when it proves completion; otherwise convert interrupted `parsing` records to `failed/parser_interrupted`, and convert `ready` records with missing/non-available parsed Artifacts to `failed/parsed_artifact_unavailable`.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_material_contracts.py tests/integration/materials/test_material_store.py tests/integration/storage/test_workflow_schema.py -q --basetemp='.cache/pytest-materials-store-green'`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```powershell
git add src/ppt_agent/domain src/ppt_agent/materials src/ppt_agent/storage/models.py migrations/versions/0009_source_materials.py tests/unit/domain/test_material_contracts.py tests/integration/materials tests/integration/storage/test_workflow_schema.py
git commit -m "feat: add durable source material state"
```

### Task 2: Safe format detection and bounded parser subprocess

**Files:**
- Create: `src/ppt_agent/materials/formats.py`
- Create: `src/ppt_agent/materials/parsers/__init__.py`
- Create: `src/ppt_agent/materials/parsers/markdown.py`
- Create: `src/ppt_agent/materials/parsers/pdf.py`
- Create: `src/ppt_agent/materials/parsers/image.py`
- Create: `src/ppt_agent/materials/worker.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/materials/test_formats.py`
- Test: `tests/unit/materials/test_markdown_parser.py`
- Test: `tests/unit/materials/test_pdf_parser.py`
- Test: `tests/unit/materials/test_image_parser.py`
- Test: `tests/integration/materials/test_parser_worker.py`

**Interfaces:**
- Consumes: `ParsedMaterialV1` and nested contracts from Task 1; `InputLimits`; a canonical source Artifact path provided only after Workspace containment checks.
- Produces: `sniff_material(head: bytes, *, declared_type: str | None, display_name: str) -> MaterialFormat`; `parse_markdown(request: ParseRequest) -> ParsedMaterialV1`; `parse_pdf(...)`; `parse_image(...)`; `ParserWorker.parse(request: ParseRequest) -> ParsedMaterialV1`; `ParserFailure(code: MaterialErrorCode, safe_detail: str)`.

- [ ] **Step 1: Add dependencies and write failing format/parser tests**

Add compatible ranges `Pillow>=11,<13` and `pypdf>=5,<7`. Tests generate fixtures locally and cover magic-byte detection, invalid UTF-8, Markdown depth/resource counts and inert HTML, PDF pages/object count/encryption/malformed/scanned pages, image pixels/frames/compression/truncation, deterministic locators, and no network access.

- [ ] **Step 2: Run parser unit tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/materials -q --basetemp='.cache/pytest-material-parsers-red'`

Expected: FAIL because parser modules do not exist.

- [ ] **Step 3: Implement content-first format detection**

Recognize PDF, PNG, JPEG, and WebP from signatures. Treat other input as Markdown only when strict UTF-8 decoding succeeds and no binary NUL is present. A declared type or extension may narrow error reporting but never override decoded content.

- [ ] **Step 4: Implement deterministic Markdown, PDF, and image parsers**

Markdown records line-span locators, list/blockquote depth, references without retrieval, and inert HTML warnings. PDF uses pypdf page and xref/object information, rejects protected encryption, extracts bounded page text, and marks empty pages `text_unavailable`. Pillow performs verified full decode, orientation-aware metadata capture, pixel/frame/decoded-memory/compression checks, and returns no pixel payload.

- [ ] **Step 5: Write failing worker isolation tests**

Cover successful spawn, timeout termination/join, child crash, invalid schema, temporary-directory cleanup, safe error mapping, output-envelope byte cap, and `test_worker_rejects_oversized_result_envelope`.

- [ ] **Step 6: Run worker tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/materials/test_parser_worker.py -q --basetemp='.cache/pytest-material-worker-red'`

Expected: FAIL because `ParserWorker` is absent.

- [ ] **Step 7: Implement `ParserWorker`**

Use multiprocessing `get_context("spawn")`, a one-way bounded message envelope, a per-attempt temporary directory under the Workspace temp boundary, timeout terminate/join/kill fallback, and schema validation in the parent. Pass only primitive request data to the child. Apply POSIX resource limits when available; document the weaker Windows boundary in code and diagnostics.

- [ ] **Step 8: Run all Task 2 tests and verify GREEN**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/materials tests/integration/materials/test_parser_worker.py -q --basetemp='.cache/pytest-material-parsers-green'`

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

```powershell
git add pyproject.toml src/ppt_agent/materials tests/unit/materials tests/integration/materials/test_parser_worker.py
git commit -m "feat: parse source materials in bounded workers"
```

### Task 3: Artifact-first material service, scheduler, recovery, and HTTP API

**Files:**
- Create: `src/ppt_agent/materials/service.py`
- Create: `src/ppt_agent/materials/supervisor.py`
- Create: `src/ppt_agent/api/materials.py`
- Modify: `src/ppt_agent/artifacts/store.py`
- Modify: `src/ppt_agent/api/app.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Test: `tests/integration/materials/test_material_service.py`
- Test: `tests/integration/materials/test_material_supervisor.py`
- Test: `tests/integration/api/test_materials.py`
- Test: `tests/integration/runtime/test_application.py`

**Interfaces:**
- Consumes: Task 1 `MaterialStore`; Task 2 `sniff_material` and `ParserWorker`; `ArtifactStore`, `CreateArtifact`, `EdgeInput`, project authorizer, local HTTP security.
- Produces: `ArtifactStore.write_staging_async(artifact_id: str, chunks: AsyncIterable[bytes], *, max_bytes: int) -> ArtifactSnapshot`; `MaterialService.upload(...) -> MaterialSnapshot`, `parse(material_id) -> MaterialSnapshot`, `retry(...)`, `delete(...)`; `MaterialSupervisor.schedule(material_id) -> bool`, `shutdown(timeout) -> tuple[str, ...]`; `materials_router(...) -> APIRouter`; optional `MaterialRuntimeFactory` composition in `ApplicationRuntime`.

- [ ] **Step 1: Write failing service and Artifact streaming tests**

Cover bounded async streaming without buffering the whole body, empty input, disconnect cleanup/reconciliation, original Artifact availability before material creation, deterministic parsed Artifact cache identity and `derived_from` edge, crash after parsed file commit, duplicate parse claim, explicit retry, logical deletion, and atomic project quota. Include `test_disconnected_upload_leaves_no_ready_material`.

- [ ] **Step 2: Run service tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/materials/test_material_service.py -q --basetemp='.cache/pytest-material-service-red'`

Expected: FAIL because streaming/service interfaces are absent.

- [ ] **Step 3: Add a bounded async Artifact staging writer**

Implement `write_staging_async` without loading the request body into memory. It uses the existing per-Artifact lock and commit invariants, rejects a chunk that would cross `max_bytes`, removes partial staging files on cancellation/error, and persists content hash/size exactly once. Keep synchronous `write_staging` behavior unchanged.

- [ ] **Step 4: Implement `MaterialService`**

`upload` authorizes inputs supplied by the API, sanitizes the display name, streams to a source Artifact, sniffs the committed bytes, finalizes the material under the project quota, and schedules only after durable state exists. `parse` claims atomically, runs the worker off the event loop, validates and serializes V1, commits `parsed_material` with `derived_from`, then completes the state. Failures persist only stable codes and sanitized details.

- [ ] **Step 5: Write failing supervisor, runtime, and API tests**

Test global/project parser limits, schedule deduplication, bounded shutdown, startup reconciliation order, authenticated upload/list/detail/retry/delete, Host/Origin/project authorization, cross-project 404, max-byte 413, safe errors, and `test_upload_uses_sniffed_type_over_claimed_type`.

- [ ] **Step 6: Run supervisor/runtime/API tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/materials/test_material_supervisor.py tests/integration/api/test_materials.py tests/integration/runtime/test_application.py -q --basetemp='.cache/pytest-material-api-red'`

Expected: FAIL because the scheduler, runtime composition, and routes are absent.

- [ ] **Step 7: Implement supervisor and runtime composition**

Build a material-specific supervisor keyed by material ID with global/project semaphores and cancellation-safe shutdown. Extend `ApplicationRuntime` with an optional material factory; run Artifact reconciliation, then material reconciliation, then workflow recovery, and stop the material supervisor before disposing the engine. Preserve existing constructor behavior when materials are not configured.

- [ ] **Step 8: Implement authenticated material routes**

Build `materials_router` with the five endpoints from the spec. Read `Request.stream()` directly, pass declared type/display name only as hints, schedule after a durable upload, and map stable service errors to 400/404/409/413/415/422/503 without exposing paths or content.

- [ ] **Step 9: Run all Task 3 tests and verify GREEN**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/materials/test_material_service.py tests/integration/materials/test_material_supervisor.py tests/integration/api/test_materials.py tests/integration/runtime/test_application.py -q --basetemp='.cache/pytest-material-api-green'`

Expected: PASS.

- [ ] **Step 10: Commit Task 3**

```powershell
git add src/ppt_agent/artifacts/store.py src/ppt_agent/materials src/ppt_agent/api src/ppt_agent/runtime/application.py tests/integration/materials tests/integration/api/test_materials.py tests/integration/runtime/test_application.py
git commit -m "feat: expose durable material ingestion"
```

### Task 4: Planning source validation, application projection, and milestone handoff

**Files:**
- Modify: `src/ppt_agent/api/planning.py`
- Modify: `src/ppt_agent/api/app.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/workflows/planning_store.py`
- Modify: `tests/integration/api/test_planning.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 `MaterialStore.resolve_ready(project_id, material_ids)` and `ResolvedMaterial`; current `PlanningWorkflow.create_run(project_id, normalized_text, source_refs)`.
- Produces: public `PlanningRunCreate(normalized_text: str = "", material_ids: tuple[str, ...] = ())`; deterministic `compile_planning_sources(typed_text: str, materials: tuple[ResolvedMaterial, ...]) -> CompiledPlanningSource`; internal validated parsed Artifact IDs passed as `source_refs`.

- [ ] **Step 1: Write failing planning integration tests**

Test typed text only, material only, mixed ordered input, empty rejection, same-project ownership, pending/failed/deleted material rejection, stale/invalid parsed Artifact rejection, source boundaries, public rejection of `source_refs`, and `test_planning_rechecks_artifact_availability_in_creation_boundary`.

- [ ] **Step 2: Run planning integration tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_planning.py tests/integration/workflows/test_deck_planning.py -q --basetemp='.cache/pytest-material-planning-red'`

Expected: FAIL because planning does not accept or resolve material IDs.

- [ ] **Step 3: Implement deterministic source compilation**

Resolve all material IDs in one immediate transaction, load their validated parsed JSON, and compile blocks in request order using canonical JSON-delimited material/locator boundaries. Preserve typed user text as a separate explicit input section. Reject a request with neither nonblank typed text nor a ready material.

- [ ] **Step 4: Integrate planning API and workflow**

Replace public `source_refs` with `material_ids`; authorize the project before resolution, pass only validated parsed Artifact IDs internally, and ensure run/input persistence occurs only after the final availability check. Existing internal direct workflow tests may continue to construct validated `source_refs`.

- [ ] **Step 5: Update repository handoff documentation**

Record the new migration, parser dependencies, APIs, limits, Windows isolation caveat, test count, and remaining Seedream/Qwen/Renderer/export work in `AGENTS.md`; update README implementation status without claiming OCR or rendering support.

- [ ] **Step 6: Run Task 4 tests and verify GREEN**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_planning.py tests/integration/workflows/test_deck_planning.py -q --basetemp='.cache/pytest-material-planning-green'`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add src/ppt_agent/api src/ppt_agent/workflows tests/integration/api/test_planning.py tests/integration/workflows/test_deck_planning.py AGENTS.md README.md
git commit -m "feat: plan decks from validated materials"
```

### Task 5: Batch verification and independent review

**Files:**
- Modify only files required by Critical or Important review findings.
- Add regression tests beside the affected modules.

**Interfaces:**
- Consumes: all Task 1–4 interfaces.
- Produces: a reviewed, migration-tested, fully green ingestion batch and a final progress checkpoint.

- [ ] **Step 1: Run focused material and planning suites**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/materials tests/unit/domain/test_material_contracts.py tests/integration/materials tests/integration/api/test_materials.py tests/integration/api/test_planning.py tests/integration/runtime/test_application.py tests/integration/workflows/test_deck_planning.py -q --basetemp='.cache/pytest-material-focused'`

Expected: PASS with no unexpected skips.

- [ ] **Step 2: Run the full suite**

Run: `& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-final'`

Expected: PASS; only environment-documented symlink and opt-in live-provider skips are allowed.

- [ ] **Step 3: Run static repository checks**

Run: `& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output and exit code 0.

- [ ] **Step 4: Request one whole-batch independent review**

Review against the design spec, this plan, migration/recovery behavior, byte and parser limits, cross-project authorization, child-process lifecycle, Artifact atomicity, and the five Review Focus cases. Re-grade findings by user impact.

- [ ] **Step 5: Fix Critical and Important findings in one TDD pass**

For every accepted finding, add a regression test, observe RED, implement the smallest root-cause fix, observe GREEN, then rerun the focused and full suites. Record deferred Minor findings in the execution ledger and final handoff.

- [ ] **Step 6: Update final test counts and commit the verified checkpoint**

Update `AGENTS.md` with observed results, rerun full tests, `compileall`, `git diff --check`, and `git status --short`, then commit only reviewed batch changes.

```powershell
git add AGENTS.md src tests migrations pyproject.toml README.md
git commit -m "docs: update material ingestion milestone handoff"
```
