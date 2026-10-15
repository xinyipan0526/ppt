# Single-Call Planning and Provider Errors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make new planning runs produce a complete reviewable plan with one model call when no clarification is needed, propagate safe Provider failures through Run/Step/API/UI, and load detailed traces only after the user opens Run Details.

**Architecture:** Introduce `deck_planning/v2` with `build_plan -> review_plan`, while the existing workflow class retains an explicit v1 execution path for unfinished legacy runs. A single `InitialPlanDecision` response is persisted before idempotent materialization into the existing immutable planning artifacts. Provider diagnostics remain bounded and redacted, unknown outcomes remain non-retryable without authorization, and the React status surface owns a lazy Run Details tab instead of mounting the trace unconditionally.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy 2, SQLite, FastAPI, httpx, React 19, TypeScript, TanStack Query, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-15-single-call-planning-and-provider-errors-design.md`

## Global Constraints

- Work on the current `feat/ppt` branch; do not create a worktree.
- Preserve all pre-existing unstaged and untracked user changes; never use `git reset --hard` or `git checkout --`.
- New planning runs use `deck_planning/v2`; persisted non-terminal v1 runs keep their original execution and recovery semantics.
- A ready initial v2 plan performs exactly one Provider Operation, one Attempt, and one network call.
- `outcome_unknown` is never automatically retried and its budget reservation remains held.
- Do not persist or expose API keys, Authorization/Cookie values, full prompts, raw Provider responses, untrusted source text, or local paths.
- Use tests first for every product-code behavior change and observe the intended RED failure before implementation.
- Because target files already contain pre-existing unstaged work, do not create product-code commits unless the staged diff can be proven to contain only this plan's changes; otherwise leave changes unstaged and report them.

## Review Focus

- A crash after Provider success but before the last PageSpec artifact must resume materialization without a second Attempt; Task 3 adds the boundary regression test.
- A legacy v1 run stopped at any existing stage must not be interpreted as v2; Task 2 adds explicit version-routing tests.
- A read timeout with no HTTP response must not invent a Request ID, status, or Provider message; Task 4 adds exact safe-summary assertions.
- A process exit between Operation `outcome_unknown` and Workflow recovery projection must be repaired by startup recovery without resubmission; Task 5 adds the compensation test.
- A closed Run Details tab must not issue trace requests, including after SSE refreshes; Task 6 adds request-count tests.

---

### Task 1: Complete-plan domain contract and versioned prompt

**Files:**
- Modify: `src/ppt_agent/domain/contracts.py`
- Create: `src/ppt_agent/skills/deck_planning/initial-plan-v1.md`
- Modify: `tests/unit/domain/test_planning_contracts.py`

**Interfaces:**
- Produces: `PlanningDraft(brief, outline, design_system, page_specs)`.
- Produces: `InitialPlanDecision(kind, plan, reason, questions)` with mutually exclusive `ready` and `clarification_required` branches.
- Produces: prompt identity `initial-plan`, version `1`, schema `initial_plan_decision`.
- Consumes: existing `PresentationBrief`, `DeckOutline`, `DesignSystem`, `PageSpecs`, and `ClarificationQuestion` contracts.

- [ ] **Step 1: Write failing contract tests**

  Add tests named `test_initial_plan_decision_accepts_one_consistent_ready_plan`, `test_initial_plan_decision_normalizes_inactive_branch`, and `test_planning_draft_rejects_cross_object_mismatch`. Assert that a valid eight-page draft round-trips, inactive clarification fields normalize away, and Brief/Outline/PageSpecs disagreement raises `ValidationError`.

- [ ] **Step 2: Run the contract tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_planning_contracts.py -q --basetemp='.cache/pytest-single-plan-contract-red'`

  Expected: FAIL because `PlanningDraft` and `InitialPlanDecision` do not exist.

- [ ] **Step 3: Implement the strict contracts**

  Add `PlanningDraft` with an `after` validator for page counts, top-level versus embedded Outline equality, and per-page objective equality. Add `InitialPlanDecision` using the existing runtime-friendly discriminated-shape pattern so Ark JSON Schema generation remains supported.

- [ ] **Step 4: Add the full-plan prompt**

  Create `initial-plan-v1.md` with the exact identity/version/schema header, the `<untrusted_source_content>` boundary, the clarification-only branch, the complete ready branch, and instructions that all four planning objects must be mutually consistent.

- [ ] **Step 5: Run the contract tests and verify GREEN**

  Run the Step 2 command with basetemp `.cache/pytest-single-plan-contract-green`.

  Expected: PASS.

- [ ] **Step 6: Checkpoint safely**

  Inspect `git diff --` for the three Task 1 files. Commit only if no pre-existing user hunk would be included; otherwise record the passing test and leave the files unstaged.

### Task 2: Version-aware planning store and executor routing

**Files:**
- Modify: `src/ppt_agent/workflows/planning_store.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `tests/integration/runtime/test_application.py`

**Interfaces:**
- Produces: `V1_PLANNING_STAGES` with the existing five stages.
- Produces: `V2_PLANNING_STAGES = ("build_plan", "review_plan")`.
- Produces: `PlanningStore.create_run(..., workflow_version: int = 2)` and version-aware `next_step(run_id)`.
- Produces: `DeckPlanningWorkflow.execute(run_id)` routing by the persisted workflow version, never by current configuration.
- Consumes: current v1 step creation, recovery keys, review protocol, and `TaskSupervisor` executor signature.

- [ ] **Step 1: Write failing version tests**

  Add tests proving a newly created run persists `workflow_version == 2`, its first step is `build_plan`, a seeded v1 run still starts/resumes `build_brief`, and runtime recovery sends each version to its matching path.

- [ ] **Step 2: Run targeted tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/workflows/test_deck_planning.py tests/integration/runtime/test_application.py -q --basetemp='.cache/pytest-planning-v2-routing-red'`

  Expected: FAIL because new runs are v1 and `build_plan` is not a valid stage.

- [ ] **Step 3: Make PlanningStore version-aware**

  Resolve the stage tuple from `WorkflowRunRecord.workflow_version`, emit `deck_planning/v2` in `run.queued`, and preserve the exact existing v1 idempotency keys and stage order. Reject unsupported versions before creating or executing a step.

- [ ] **Step 4: Split execution paths without duplicating composition**

  Keep the public `DeckPlanningWorkflow` dependency surface used by API/runtime composition. Move the current loop behind an explicit v1 path and add a v2 dispatch point; do not reinterpret existing Step rows or copy Provider attempts.

- [ ] **Step 5: Run targeted tests and verify GREEN**

  Run the Step 2 command with basetemp `.cache/pytest-planning-v2-routing-green`.

  Expected: PASS for version routing; v2 execution may stop at the not-yet-implemented `build_plan` branch covered in Task 3.

- [ ] **Step 6: Checkpoint safely**

  Inspect only Task 2 diffs and follow the Global Constraints staging rule.

### Task 3: One-call v2 planning, artifact materialization, review, and revision

**Files:**
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/workflows/planning_store.py`
- Modify: `src/ppt_agent/api/planning.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `tests/integration/api/test_planning.py`
- Modify: `tests/e2e/test_deterministic_deck.py`

**Interfaces:**
- Produces: `_build_plan(run_id: str, step: PlanningStep) -> bool` using `InitialPlanDecision` and prompt `initial-plan-v1`.
- Produces: idempotent materialization of Brief, Outline, DesignSystem, PageSpecs aggregate, and individual PageSpec artifacts from one Provider result.
- Produces: version-aware planning reference lookup used by review and plan-detail API.
- Consumes: `TextOperationRunner.execute`, `provider_text_results` replay, existing ArtifactStore multi-stage commit, existing plan-review Interrupt, and existing revision scope.

- [ ] **Step 1: Write the failing one-call happy-path test**

  Build a valid `InitialPlanDecision` from the real test provider, execute a new v2 run, and assert one provider call, one Operation/Attempt, all planning artifacts, and a pending plan-review Interrupt.

- [ ] **Step 2: Run the happy-path test and verify RED**

  Run its exact pytest node with basetemp `.cache/pytest-single-plan-workflow-red`.

  Expected: FAIL because `_build_plan` and v2 artifact materialization are missing.

- [ ] **Step 3: Implement the minimal ready-path workflow**

  Compile one request, validate `InitialPlanDecision`, persist the Provider result through the runner, materialize all artifact kinds with existing logical IDs and dependency edges, complete `build_plan` only after every artifact is available, then create `review_plan`.

- [ ] **Step 4: Run the happy-path test and verify GREEN**

  Re-run the Step 2 node with a green basetemp.

- [ ] **Step 5: Write failing clarification and revision tests**

  Assert that `clarification_required` creates the existing typed Interrupt without partial planning artifacts; Resume creates one new generation and one full-plan call. Assert that one review revision action makes at most one model call, with PageSpecPatch preserving unselected page refs.

- [ ] **Step 6: Implement clarification and one-call revision behavior**

  Reuse the existing form validation and revision scope. Full revisions return `PlanningDraft`; targeted page revisions return `PageSpecPatch`. Keep immutable old versions and deterministic invalidation.

- [ ] **Step 7: Write failing crash-boundary recovery tests**

  Parameterize exits after Provider result persistence and after each planning artifact commit. On resume, assert one Attempt total, all artifacts available, and exactly one final review Interrupt.

- [ ] **Step 8: Implement idempotent result replay/materialization**

  Discover existing artifacts by operation ID plus logical identity, validate their envelope before reuse, and create only missing artifacts. Never call the Provider when a selected persisted text result exists.

- [ ] **Step 9: Update API and deterministic E2E expectations**

  Make plan-detail projection resolve both v1 stage outputs and v2 build-plan outputs. Keep the client payload unchanged and prove the deterministic project-to-review flow still returns the four existing planning projections.

- [ ] **Step 10: Run the planning/API/E2E tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_planning_contracts.py tests/integration/workflows/test_deck_planning.py tests/integration/api/test_planning.py tests/e2e/test_deterministic_deck.py -q --basetemp='.cache/pytest-single-plan-workflow-green'`

  Expected: PASS.

- [ ] **Step 11: Checkpoint safely**

  Inspect only Task 3 files and follow the Global Constraints staging rule.

### Task 4: Bounded Provider diagnostics and non-duplicated logs

**Files:**
- Modify: `src/ppt_agent/providers/text.py`
- Modify: `src/ppt_agent/providers/ark.py`
- Modify: `src/ppt_agent/operations/provider.py`
- Modify: `src/ppt_agent/operations/text_runner.py`
- Modify: `src/ppt_agent/observability/logging.py`
- Modify: `tests/contract/providers/test_ark_multimodal.py`
- Modify: `tests/integration/providers/test_text_runner.py`
- Modify: `tests/unit/observability/test_logging.py`
- Modify: `tests/integration/runtime/test_application_logging.py`

**Interfaces:**
- Produces: safe Provider summary fields `request_phase`, `timeout_seconds`, `elapsed_seconds`, `provider_response_received`, optional `provider_request_id`, existing `http_status`, `provider_error_type`, and bounded `provider_error_message`.
- Produces: exactly one submission and one terminal JSONL record per text Attempt.
- Consumes: existing `ProviderCallError.safe_summary`, summary allowlist, log redaction, and 90-second Ark timeout.

- [ ] **Step 1: Write failing Ark timeout tests**

  Simulate `httpx.ConnectTimeout`, `WriteTimeout`, `ReadTimeout`, and `PoolTimeout`. For `ReadTimeout`, assert `unknown_remote_outcome`, phase `read`, timeout `90`, response received `false`, and absence of HTTP status, Provider message, and Request ID.

- [ ] **Step 2: Run Provider contract tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_ark_multimodal.py tests/integration/providers/test_text_runner.py -q --basetemp='.cache/pytest-provider-diagnostics-red'`

  Expected: FAIL because the new safe fields are not produced or retained.

- [ ] **Step 3: Implement timeout/error normalization**

  Map each httpx timeout subclass to a request phase, capture the configured threshold and monotonic elapsed time, set `provider_response_received` truthfully, and retain Provider Request ID only when received. Extend the persistence allowlist without accepting arbitrary keys.

- [ ] **Step 4: Add terminal-call logging tests and implementation**

  Test success, known failure, and unknown outcome terminal logs for elapsed time and safe classification. Keep content out of logs. Add a regression proving repeated runtime/configuration setup cannot attach two active PPT Agent file handlers for the same Workspace log file.

- [ ] **Step 5: Run Provider and logging tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_ark_multimodal.py tests/integration/providers/test_text_runner.py tests/unit/observability/test_logging.py tests/integration/runtime/test_application_logging.py -q --basetemp='.cache/pytest-provider-diagnostics-green'`

  Expected: PASS with one file-log record per emitted event.

- [ ] **Step 6: Checkpoint safely**

  Inspect only Task 4 files and follow the Global Constraints staging rule.

### Task 5: Atomic Run/Step error projection and recovery compensation

**Files:**
- Modify: `src/ppt_agent/runtime/repository.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/runtime/recovery.py`
- Modify: `src/ppt_agent/events/trace.py`
- Modify: `src/ppt_agent/api/workspace.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `tests/integration/runtime/test_application.py`
- Modify: `tests/integration/api/test_run_trace.py`
- Modify: `tests/integration/api/test_product_workspace_api.py`

**Interfaces:**
- Produces: `WorkflowRepository.mark_run_recovery_required(run_id, *, reason, error=None, step_id=None, operation_id=None)` that atomically projects the same safe error to the active Step and Run and emits `run.recovery_required` with resource IDs.
- Produces: startup compensation from an unknown Operation to missing Step/Run recovery state.
- Consumes: normalized Provider summaries from Task 4 and existing snapshot/trace serializers.

- [ ] **Step 1: Write failing workflow error-projection tests**

  Raise a `ReadTimeout`-shaped `ProviderCallError` from the v2 plan call. Assert Attempt/Operation unknown state, held reservation, Step and Run `recovery_required`, matching structured errors, and a durable event carrying step/operation IDs.

- [ ] **Step 2: Run targeted tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/workflows/test_deck_planning.py tests/integration/api/test_run_trace.py tests/integration/api/test_product_workspace_api.py -q --basetemp='.cache/pytest-error-projection-red'`

  Expected: FAIL because current recovery-required state leaves Run/Step error empty.

- [ ] **Step 3: Implement atomic Workflow projection**

  Extend the repository transition to update the active Step, Run, recovery metadata, and event in one SQLite transaction. Pass `safe_provider_failure_summary()` from the planning workflow instead of only the code string. Ensure repeated projection is idempotent.

- [ ] **Step 4: Write the failing recovery-window test**

  Seed an `outcome_unknown` Operation while its Step/Run still look running, invoke startup recovery, and assert it repairs both to `recovery_required` without executing the Provider.

- [ ] **Step 5: Implement recovery compensation**

  Make the recovery coordinator treat an unknown linked Operation as authoritative evidence for recovery-required projection, retain the budget hold, and never enqueue ordinary execution before the projection exists.

- [ ] **Step 6: Verify snapshot and trace serialization**

  Assert the safe fields from Task 4 appear in Run Snapshot, Step projection, and Trace Attempt summary while sensitive and unknown keys remain absent.

- [ ] **Step 7: Run backend error tests and verify GREEN**

  Re-run the Step 2 command plus `tests/integration/runtime/test_application.py` with basetemp `.cache/pytest-error-projection-green`.

  Expected: PASS.

- [ ] **Step 8: Checkpoint safely**

  Inspect only Task 5 files and follow the Global Constraints staging rule.

### Task 6: Product loading state, human-readable errors, and lazy Run Details

**Files:**
- Modify: `web/src/api/contracts.ts`
- Modify: `web/src/features/runs/errorPresentation.ts`
- Modify: `web/src/features/runs/errorPresentation.test.ts`
- Modify: `web/src/features/runs/RunStatus.tsx`
- Modify: `web/src/features/runs/RunStatus.test.tsx`
- Modify: `web/src/features/runs/RunTrace.tsx`
- Modify: `web/src/features/runs/RunTrace.test.tsx`
- Modify: `web/src/styles/global.css`
- Modify: `web/e2e/product-flow.spec.ts`

**Interfaces:**
- Produces: expanded TypeScript `SafeProviderSummary` matching the backend allowlist.
- Produces: a default task surface with spinner/progress, current stage, result/error, and explicit `运行详情` tab/button.
- Produces: lazy `RunTrace` mounting/querying only while details are selected.
- Consumes: Run Snapshot as the normal product source and Trace only for diagnostics/recovery authorization.

- [ ] **Step 1: Write failing error-copy tests**

  Assert a read-timeout error formats to Chinese copy containing the 90-second threshold, no verified Provider response, unknown remote outcome, and no automatic retry. Assert known authentication/parameter errors retain their existing actionable copy.

- [ ] **Step 2: Run the formatter test and verify RED**

  Run: `npm test -- src/features/runs/errorPresentation.test.ts` from `web`.

  Expected: FAIL because TypeScript contracts and formatter ignore phase/timeout/response-received fields.

- [ ] **Step 3: Expand contracts and formatter**

  Add only backend-allowlisted optional fields. Prefer the read-timeout-specific message when `request_phase === "read"` and `provider_response_received === false`; never render absent Request IDs or raw Provider messages.

- [ ] **Step 4: Write failing lazy-details tests**

  Render a running and a recovery-required RunStatus. Assert no `/trace` request and no trace lists before clicking `运行详情`, including after an SSE refresh callback. Click the control and assert one cached trace request, visible details, reachable unknown-result authorization, and that trace failure leaves the main error visible.

- [ ] **Step 5: Run RunStatus tests and verify RED**

  Run: `npm test -- src/features/runs/RunStatus.test.tsx src/features/runs/RunTrace.test.tsx` from `web`.

  Expected: FAIL because RunTrace is mounted and polled unconditionally.

- [ ] **Step 6: Implement the product status and Run Details interaction**

  Remove unconditional RunTrace mounting and trace invalidation while details are closed. Use a clear loading indicator and stage label in the default surface. Move Operation-specific recovery controls into details; the main recovery error presents an explicit action that opens details so safety actions remain reachable.

- [ ] **Step 7: Update styling and browser E2E**

  Add accessible selected-tab state, focus behavior, and responsive spacing. Extend the deterministic E2E to prove the trace is absent by default and appears after the user opens Run Details.

- [ ] **Step 8: Run frontend tests, typecheck, and build**

  From `web`, run:

  - `npm test`
  - `npm run typecheck`
  - `npm run build`

  Expected: all PASS and new hashed assets are generated under `src/ppt_agent/static` by the existing build configuration.

- [ ] **Step 9: Checkpoint safely**

  Inspect all generated static-asset changes together with Task 6 source changes and follow the Global Constraints staging rule.

### Task 7: Documentation, regression suite, and release evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/ui-guide.md`
- Modify: `docs/provider-setup.md`
- Modify: `AGENTS.md` only if the verified test counts or implementation checkpoint table must be updated
- Verify: all files changed by Tasks 1–6

**Interfaces:**
- Produces: user documentation describing one-call initial planning, truthful timeout behavior, manual unknown-result recovery, and on-demand Run Details.
- Consumes: all implemented backend/frontend behavior and built static assets.

- [ ] **Step 1: Update user-facing documentation**

  Remove descriptions that imply four normal planning calls or always-visible traces. Document that clarification can add a second call and that timeouts with unknown remote outcome require explicit user action.

- [ ] **Step 2: Run focused backend suites**

  Run the Task 3, Task 4, and Task 5 green commands with fresh basetemp directories.

  Expected: PASS.

- [ ] **Step 3: Run the full Python suite**

  Run: `& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-single-plan-final'`

  Expected: all tests PASS; only the documented platform/live-test skips remain.

- [ ] **Step 4: Run the full frontend and Chromium suites**

  From `web`, run `npm test`, `npm run typecheck`, `npm run build`, and `npm run e2e`.

  Expected: all PASS.

- [ ] **Step 5: Run repository integrity checks**

  Run:

  - `& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations`
  - `git diff --check`
  - `git status --short`

  Expected: compileall and diff check exit 0; status contains no unexplained files and preserves unrelated pre-existing changes.

- [ ] **Step 6: Review the complete diff against the spec**

  Confirm every requirement in the design spec maps to code and a passing test, no v1 recovery behavior regressed, no trace request occurs while details are closed, and no sensitive Provider data entered logs/events/API/UI.

- [ ] **Step 7: Create a recoverable implementation checkpoint only if safe**

  If and only if task changes can be staged without including pre-existing user work, stage the exact implementation files and commit with a scoped message such as `feat: add single-call planning and provider error UX`. Otherwise leave the implementation unstaged and provide the user with the exact verification evidence and overlap explanation.
