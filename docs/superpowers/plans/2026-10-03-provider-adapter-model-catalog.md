# Provider Adapter and Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed DeepSeek/Seedream/Qwen cloud configuration with one Volcengine Ark credential and one catalog-selected model for each of the multimodal and image capability groups.

**Architecture:** Workflows continue to consume the existing structured-text, vision, OCR, and image protocols. A versioned model catalog supplies capabilities, safe endpoints, result hosts, and pricing; one Ark multimodal adapter implements text/vision/OCR over Responses API and the Ark image adapter implements Seedream generation/editing. Runtime configuration validates and freezes the selected catalog entries per run while preserving all Operation/Attempt, budget, secret, and unknown-outcome guarantees.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, httpx, SQLAlchemy/SQLite, React 19, TypeScript, Zod, TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-03-provider-adapter-model-catalog-design.md`

## Global Constraints

- Work on the current `feat/ppt` branch; do not create a worktree.
- Do not touch or commit the untracked `workspace/` directory.
- Only `api_key`, `multimodal_model_id`, and `image_model_id` are required user inputs.
- First-party IDs are `doubao-seed-2-1-pro-260915`, `doubao-seed-2-1-lite-260915`, experimental `deepseek-v4-1-flash-260910`, and `doubao-seedream-5-0-pro-260628`.
- The exact `model_id` string is the only persisted and submitted model identity; UI labels and internal `catalog_id` values must never be accepted in `multimodal_model_id` or `image_model_id`.
- The only first-release adapter is `volcengine_ark`, using `https://ark.cn-beijing.volces.com/api/v3`.
- A cloud runtime requires one production-qualified multimodal model and one production-qualified image model; Qwen is not required.
- API keys must not enter responses, SQLite, events, logs, URLs, browser storage, or query cache.
- Preserve atomic reservations, charged-failure settlement, and `outcome_unknown` no-resend behavior.
- Existing provider configuration and secret files must remain recoverable and must not be silently deleted.
- Final production delivery must rebuild `web`, update `src/ppt_agent/static`, reinstall the local `.venv` package, and require a service restart.

## Review Focus

- A catalog ID from the wrong category or an experimental model must fail before any secret or active configuration is replaced; pin in Task 1 and Task 4 tests.
- A failed candidate-secret write or remote qualification check must leave both the previous key and active runtime intact; pin in Task 4 tests.
- A Responses API success with usage but malformed JSON/Schema must settle the charged usage and never become an unknown free retry; pin in Task 2 and existing runner regression tests.
- Seedream 5.0 Pro reservations must cover output pixel tier plus chargeable reference images rather than a flat per-image guess; pin in Task 3 tests.
- An installed, non-editable package must not be described as hot-reloading; pin the build/install behavior in Task 7 documentation and verify the imported package path after reinstall.

---

### Task 1: Versioned model catalog and capability qualification

**Files:**
- Create: `src/ppt_agent/providers/catalog.py`
- Modify: `src/ppt_agent/providers/capabilities.py`
- Modify: `src/ppt_agent/providers/__init__.py`
- Create: `tests/unit/providers/test_catalog.py`
- Modify: `tests/unit/providers/test_capabilities.py`

**Interfaces:**
- Produces: `ModelCategory`, `ModelLifecycle`, `ModelCapability`, `TokenPricing`, `ImagePricingSchedule`, `ModelCatalogEntry`, `ModelCatalog`.
- Produces: `DEFAULT_MODEL_CATALOG: ModelCatalog`, `ModelCatalog.require(model_id: str, category: ModelCategory, *, allow_experimental: bool = False) -> ModelCatalogEntry`, and `ModelCatalog.public_snapshot() -> tuple[dict[str, object], ...]`.
- Consumes: existing `ProviderCapabilities` request-limit structures where useful; no Workflow imports.

- [ ] **Step 1: Write catalog tests that pin exact model IDs, categories, capabilities, recommendation status, lifecycle, endpoint, result hosts, unique IDs, and non-negative exact pricing values.**

  Include `test_catalog_rejects_wrong_category_and_experimental_selection`, asserting that Seedream cannot satisfy `multimodal` and DeepSeek V4.1 Flash is rejected unless `allow_experimental=True`. Include `test_catalog_rejects_labels_aliases_and_case_variants`, asserting that `Doubao Seed 2.1 Pro`, `doubao-seed-2.1-pro`, `DOUBAO-SEED-2-1-PRO-260915`, and internal catalog IDs are rejected.

- [ ] **Step 2: Run the catalog tests and verify they fail because `providers.catalog` does not exist.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/providers/test_catalog.py tests/unit/providers/test_capabilities.py -q --basetemp='.cache/pytest-provider-catalog-red'`

- [ ] **Step 3: Implement immutable catalog types and the four approved entries.**

  Use integer CNY fen per million tokens: Seed 2.1 Pro `600/3000`, Seed 2.1 Lite `80/270`, and experimental DeepSeek V4.1 Flash `150/450` for input/output. Represent Seedream pricing as output tiers of `30` fen at `<=2_610_000` pixels and `60` fen above, plus `2` fen for every reference image after the first; record the official pricing URL and `official-cn-2026-10-03` as the version. Do not add arbitrary/custom model entries.

- [ ] **Step 4: Run the catalog tests and verify all pass.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/providers/test_catalog.py tests/unit/providers/test_capabilities.py -q --basetemp='.cache/pytest-provider-catalog-green'`

- [ ] **Step 5: Commit the catalog checkpoint.**

  ```powershell
  git add src/ppt_agent/providers tests/unit/providers
  git commit -m "feat: add versioned Ark model catalog"
  ```

### Task 2: Volcengine Ark multimodal Responses adapter

**Files:**
- Create: `src/ppt_agent/providers/ark.py`
- Retain unchanged for compatibility: `src/ppt_agent/providers/deepseek.py`
- Retain unchanged for compatibility: `src/ppt_agent/providers/qwen.py`
- Create: `tests/contract/providers/test_ark_multimodal.py`
- Modify: `tests/integration/providers/test_text_runner.py`
- Modify: `tests/integration/providers/test_vision_runner.py`

**Interfaces:**
- Consumes: `StructuredTextRequest`, `VisionRequest`, `OCRRequest` and their result/error types.
- Produces: `ArkMultimodalAdapter(api_key: str, base_url: str = ARK_BASE_URL, *, client: httpx.AsyncClient | None = None, timeout_seconds: float = 90.0)` implementing `complete`, `analyze`, and `recognize`.
- Produces: shared private response parsing that maps Ark Responses usage into `TextUsage` or `VisionUsage` without exposing prompts or keys.

- [ ] **Step 1: Write contract tests for structured text, image input, OCR spans, normalized region coordinates, usage details, HTTP error classes, malformed charged success, and request exceptions.**

  Assert Ark requests use `/responses`, Bearer authentication, `input_text`/`input_image` content, and `text.format={type: "json_schema", strict: true}`. Assert request exceptions produce `unknown_remote_outcome`; explicit 400/401/402/422 responses are known uncharged; malformed success with usage is charged `content_rejected`.

- [ ] **Step 2: Run the contract and runner tests and verify they fail for the missing adapter and the text runner's hard-coded `deepseek` provider guard.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_ark_multimodal.py tests/integration/providers/test_text_runner.py tests/integration/providers/test_vision_runner.py -q --basetemp='.cache/pytest-ark-multimodal-red'`

- [ ] **Step 3: Implement `ArkMultimodalAdapter` and make `TextOperationRunner` validate against `operation.provider` supplied by composition rather than the literal `deepseek`.**

  Change `TextOperationRunner.__init__` to accept `provider_name: str = "deepseek"` for backward-compatible deterministic/unit fixtures; require that name in `_validate_configuration`.

- [ ] **Step 4: Run the contract and runner tests and verify all pass, including charged malformed-output settlement.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_ark_multimodal.py tests/integration/providers/test_text_runner.py tests/integration/providers/test_vision_runner.py -q --basetemp='.cache/pytest-ark-multimodal-green'`

- [ ] **Step 5: Commit the multimodal adapter checkpoint.**

  ```powershell
  git add src/ppt_agent/providers/ark.py src/ppt_agent/operations/text_runner.py tests/contract/providers/test_ark_multimodal.py tests/integration/providers
  git commit -m "feat: add Ark multimodal adapter"
  ```

### Task 3: Seedream 5.0 Pro adapter and tiered image cost accounting

**Files:**
- Modify: `src/ppt_agent/providers/seedream.py`
- Modify: `src/ppt_agent/operations/image_runner.py`
- Modify: `tests/contract/providers/test_seedream.py`
- Modify: `tests/integration/providers/test_image_runner.py`
- Create: `tests/unit/providers/test_image_pricing.py`

**Interfaces:**
- Consumes: `ModelCatalogEntry.image_pricing` from Task 1.
- Produces: `ImagePricing.quote(*, width: int, height: int, generated_images: int, input_images: int) -> int` and `ImagePricing.reserve(request: ImageGenerationRequest) -> int`.
- Keeps: `SeedreamProvider.generate(request: ImageGenerationRequest) -> ImageGenerationResult`, updated for the 5.0 Pro parameter contract.

- [ ] **Step 1: Add failing tests for the exact Seedream 5.0 Pro model ID, single-output request shape, 1.5K pixel boundary, high-pixel price tier, first free reference image, additional reference-image charges, and usage settlement.**

- [ ] **Step 2: Run the image tests and verify the flat `per_image_minor_units` implementation fails the tiered assertions.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/providers/test_image_pricing.py tests/contract/providers/test_seedream.py tests/integration/providers/test_image_runner.py -q --basetemp='.cache/pytest-seedream5-red'`

- [ ] **Step 3: Replace flat image pricing with the catalog-backed schedule and update Seedream request/result parsing without weakening result download validation.**

  Preserve URL-or-base64 exclusivity, `response_format`, HTTPS-only endpoint validation, and unknown-outcome mapping. Do not send `sequential_image_generation` for Seedream 5.0 Pro because the official API does not support that option.

- [ ] **Step 4: Run the image tests and verify all pass.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/providers/test_image_pricing.py tests/contract/providers/test_seedream.py tests/integration/providers/test_image_runner.py -q --basetemp='.cache/pytest-seedream5-green'`

- [ ] **Step 5: Commit the image adapter and pricing checkpoint.**

  ```powershell
  git add src/ppt_agent/providers/seedream.py src/ppt_agent/operations/image_runner.py tests/contract/providers/test_seedream.py tests/integration/providers/test_image_runner.py tests/unit/providers/test_image_pricing.py
  git commit -m "feat: support Seedream 5 tiered billing"
  ```

### Task 4: Versioned runtime configuration, one Ark secret, and safe migration

**Files:**
- Modify: `src/ppt_agent/config/settings.py`
- Modify: `src/ppt_agent/config/provider_secrets.py`
- Rewrite: `src/ppt_agent/runtime/provider_config.py`
- Modify: `config/ppt-agent.example.toml`
- Modify: `.env.example`
- Modify: `tests/unit/config/test_settings.py`
- Modify: `tests/unit/config/test_provider_secrets.py`
- Create: `tests/unit/runtime/test_provider_config.py`
- Modify: `tests/integration/api/test_provider_settings_api.py`

**Interfaces:**
- Consumes: `DEFAULT_MODEL_CATALOG` from Task 1.
- Produces: settings fields `adapter_id`, `ark_api_key`, `multimodal_model_id`, `image_model_id`, and optional `pricing_overrides`; legacy fields remain readable for migration but do not qualify cloud mode.
- Produces: `ModelRuntimeSnapshot` with mode, activation, adapter status, selected catalog entries, configuration version, and redacted public projection.
- Produces: `ProviderConfigurationService.update(*, ark_api_key: str | None, multimodal_model_id: str, image_model_id: str, pricing_overrides: dict[str, object] | None = None) -> ModelRuntimeSnapshot`.

- [ ] **Step 1: Write failing tests for one-key qualification, missing-field deterministic fallback, wrong-category/experimental rejection, old-file migration, old-secret preservation, atomic secret rollback, pending activation, and redacted snapshots.**

  Include a failure injected after candidate secret replacement and assert both the previous secret value and active snapshot are unchanged. Assert persisted JSON contains exactly `doubao-seed-2-1-pro-260915` and `doubao-seedream-5-0-pro-260628`, not display labels or catalog IDs.

- [ ] **Step 2: Run configuration tests and verify they fail against the fixed three-provider schema.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/config tests/unit/runtime/test_provider_config.py tests/integration/api/test_provider_settings_api.py -q --basetemp='.cache/pytest-model-runtime-red'`

- [ ] **Step 3: Implement schema-v2 persistence and move the active secret path to `.secrets/adapters/volcengine_ark.key`.**

  Keep legacy files untouched. Persist only non-secret catalog IDs and optional overrides. Make validation derive endpoint, hosts, prices, and capabilities from the catalog rather than request fields.

- [ ] **Step 4: Run configuration tests and verify all pass.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/config tests/unit/runtime/test_provider_config.py tests/integration/api/test_provider_settings_api.py -q --basetemp='.cache/pytest-model-runtime-green'`

- [ ] **Step 5: Commit the configuration checkpoint.**

  ```powershell
  git add src/ppt_agent/config src/ppt_agent/runtime/provider_config.py config/ppt-agent.example.toml .env.example tests/unit/config tests/unit/runtime/test_provider_config.py tests/integration/api/test_provider_settings_api.py
  git commit -m "feat: configure cloud runtime with one Ark key"
  ```

### Task 5: Runtime composition and frozen capability bundle

**Files:**
- Create: `src/ppt_agent/providers/registry.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `tests/integration/runtime/test_application.py`
- Modify: `tests/integration/runtime/test_full_application.py`
- Modify: `tests/integration/materials/test_material_service.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `tests/integration/workflows/test_deck_production.py`
- Modify: `tests/integration/workflows/test_page_editing_workflow.py`

**Interfaces:**
- Consumes: `ModelRuntimeSnapshot`, `ArkMultimodalAdapter`, `SeedreamProvider`, and catalog pricing.
- Produces: `ProviderBundle` containing `structured_text`, `vision`, `ocr`, `image`, provider identity, selected model IDs, pricing, and result downloader.
- Produces: `AdapterRegistry.build(snapshot: ModelRuntimeSnapshot) -> ProviderBundle`.
- Changes: `BackendWorkflowDispatcher` and material analyzer receive a frozen `ProviderBundle` instead of constructing named providers from mutable Settings.

- [ ] **Step 1: Add failing integration tests proving one Ark adapter supplies planning, material vision, OCR, region location, generation, and editing, while an existing Run keeps its original bundle after a settings update.**

- [ ] **Step 2: Run focused runtime/workflow tests and verify fixed DeepSeek/Qwen/Seedream construction fails.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/runtime/test_application.py tests/integration/runtime/test_full_application.py tests/integration/materials/test_material_service.py tests/integration/workflows/test_deck_planning.py tests/integration/workflows/test_deck_production.py tests/integration/workflows/test_page_editing_workflow.py -q --basetemp='.cache/pytest-provider-bundle-red'`

- [ ] **Step 3: Implement the registry and inject immutable bundles through composition.**

  Use provider identity `volcengine_ark` for all cloud attempts, selected multimodal ID for text/vision/OCR, selected image ID for generation/editing, catalog hosts for `ResultDownloader`, and deterministic providers unchanged when configuration is incomplete.

- [ ] **Step 4: Run focused runtime/workflow tests and verify all pass.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/runtime/test_application.py tests/integration/runtime/test_full_application.py tests/integration/materials/test_material_service.py tests/integration/workflows/test_deck_planning.py tests/integration/workflows/test_deck_production.py tests/integration/workflows/test_page_editing_workflow.py -q --basetemp='.cache/pytest-provider-bundle-green'`

- [ ] **Step 5: Remove obsolete production imports/usages of `DeepSeekProvider`, `QwenVisionProvider`, and `QwenOCRProvider`; keep their modules and contract tests as explicit compatibility coverage for this release.**

- [ ] **Step 6: Commit the runtime checkpoint.**

  ```powershell
  git add src/ppt_agent/providers src/ppt_agent/runtime tests/integration/runtime tests/integration/materials/test_material_service.py tests/integration/workflows
  git commit -m "refactor: compose workflows from provider capabilities"
  ```

### Task 6: Model catalog/settings API and three-field React experience

**Files:**
- Rewrite: `src/ppt_agent/api/settings.py`
- Modify: `src/ppt_agent/api/providers.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `tests/integration/api/test_provider_settings_api.py`
- Modify: `tests/integration/api/test_browser_session.py`
- Modify: `web/src/api/contracts.ts`
- Rewrite: `web/src/features/settings/providerSchemas.ts`
- Rewrite: `web/src/features/settings/ProviderSetup.tsx`
- Rewrite: `web/src/features/settings/ProviderSetup.test.tsx`

**Interfaces:**
- Produces HTTP: `GET /api/v1/model-catalog`, `GET/PUT /api/v1/settings/model-runtime`, `POST /api/v1/adapters/volcengine_ark/test`, and `DELETE /api/v1/adapters/volcengine_ark/credential`.
- Produces frontend types: `ModelCatalogItem`, `ModelRuntimeSnapshot`, and `ModelRuntimeForm`.
- Consumes: redacted `ModelRuntimeSnapshot.public_projection()` and `ModelCatalog.public_snapshot()`.

- [ ] **Step 1: Write failing API tests for catalog output, the three-field request, secret redaction, CSRF, category validation, optional advanced values, connection-test error classification, and credential deletion.**

- [ ] **Step 2: Run API tests and verify the old five-price/three-key payload fails the new contract.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_provider_settings_api.py tests/integration/api/test_browser_session.py -q --basetemp='.cache/pytest-model-settings-api-red'`

- [ ] **Step 3: Implement the new authenticated API and composition routes, keeping an explicit deprecation response for old settings mutations.**

  The connection test must not mutate active configuration. Return only safe codes such as `authentication_error`, `model_unavailable`, `capability_mismatch`, and `unknown_remote_outcome`.

- [ ] **Step 4: Run API tests and verify all pass.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_provider_settings_api.py tests/integration/api/test_browser_session.py -q --basetemp='.cache/pytest-model-settings-api-green'`

- [ ] **Step 5: Write failing React tests for one password field, two catalog dropdowns, recommended defaults, optional advanced section, submit/clear behavior, safe errors, and absence of browser-storage writes.**

  Assert the visible labels are friendly names while the two submitted values are exactly `doubao-seed-2-1-pro-260915` and `doubao-seedream-5-0-pro-260628`.

- [ ] **Step 6: Run React tests and verify they fail against the old three-key/price form.**

  Run: `npm test -- --run web/src/features/settings/ProviderSetup.test.tsx` from `web`; if Vitest rejects the repo-relative path, run `npm test -- ProviderSetup.test.tsx`.

- [ ] **Step 7: Implement the three-field settings experience and updated API contracts.**

  The primary form contains only Ark API Key, multimodal select, image select, and “测试并启用”. Each option displays `display_name` and uses the exact `model_id` as its value. Advanced controls are optional and collapsed; no price or host field appears in the normal form.

- [ ] **Step 8: Run React tests and type checking.**

  Run from `web`: `npm test -- ProviderSetup.test.tsx` and `npm run typecheck`.
  Expected: all pass with no TypeScript errors.

- [ ] **Step 9: Commit the API/UI checkpoint.**

  ```powershell
  git add src/ppt_agent/api src/ppt_agent/runtime/composition.py tests/integration/api web/src
  git commit -m "feat: add catalog-driven model setup"
  ```

### Task 7: Documentation, production Web assets, full verification, review, and local installation

**Files:**
- Modify: `README.md`
- Modify: `docs/quickstart.md`
- Rewrite: `docs/provider-setup.md`
- Modify: `docs/configuration.md`
- Modify: `AGENTS.md`
- Modify: `web/e2e/secret-safety.spec.ts`
- Modify: `web/e2e/product-flow.spec.ts`
- Replace generated assets: `src/ppt_agent/static/index.html`, `src/ppt_agent/static/assets/*`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: an installed local package whose imported `ppt_agent` and static assets match the verified repository revision.

- [ ] **Step 1: Update user documentation and AGENTS.md progress so setup requires only the Ark key and two catalog model IDs, and explain dev HMR versus packaged static assets.**

- [ ] **Step 2: Run all focused provider/config/runtime/API tests together before the full suite.**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/providers tests/unit/config tests/unit/runtime/test_provider_config.py tests/contract/providers tests/integration/providers tests/integration/api/test_provider_settings_api.py tests/integration/runtime tests/integration/workflows -q --basetemp='.cache/pytest-provider-refactor-focused'`
  Expected: all pass; only documented environment skips are allowed.

- [ ] **Step 3: Run the full Python suite.**

  Run: `& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-provider-refactor-full'`
  Expected: all tests pass; only link-permission and opt-in paid live tests may skip.

- [ ] **Step 4: Run the complete React suite, type checking, and production build.**

  Run from `web`: `npm test`, `npm run typecheck`, and `npm run build`.
  Expected: all tests pass and `web/dist` is produced.

- [ ] **Step 5: Mechanically synchronize `web/dist` into `src/ppt_agent/static` and run Chromium product E2E against the rebuilt app.**

  Remove only the old content-hashed files inside the verified `src/ppt_agent/static/assets` target, copy the new build output, then run from `web`: `npm run e2e`.

- [ ] **Step 6: Run final static verification.**

  ```powershell
  & '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
  git diff --check
  git status --short
  ```

  Expected: compileall and diff check exit zero; `workspace/` remains untracked and is the only unrelated path.

- [ ] **Step 7: Perform one independent whole-branch code review and fix every Critical or Important finding with regression tests.**

  Re-run the smallest affected suite after each fix, then repeat Steps 2 through 6 if any production code changed.

- [ ] **Step 8: Commit the release-ready checkpoint.**

  ```powershell
  git add README.md docs AGENTS.md src/ppt_agent/static src tests web config .env.example
  git commit -m "feat: ship Ark model selection experience"
  ```

- [ ] **Step 9: Reinstall the verified repository into the existing non-editable `.venv` and verify the import location and packaged Web assets.**

  ```powershell
  & '.\.venv\Scripts\python.exe' -m pip install --no-cache-dir --force-reinstall .
  & '.\.venv\Scripts\python.exe' -c "import ppt_agent, pathlib; print(pathlib.Path(ppt_agent.__file__).resolve())"
  & '.\.venv\Scripts\ppt-agent.exe' doctor
  ```

  Expected: import resolves below `.venv/Lib/site-packages/ppt_agent`, doctor succeeds, and the user only needs to restart `ppt-agent serve` and refresh the browser.
