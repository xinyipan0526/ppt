# Runnable Deck Planning Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-usable `deck_planning/v1` workflow that calls DeepSeek, interrupts for structured clarification, persists versioned planning Artifacts, resumes deterministically, and finishes only after user approval.

**Architecture:** A provider-independent structured-text protocol feeds a DeepSeek Responses API adapter through the existing durable Operation/Attempt and cost ledger. A deterministic workflow owns the fixed Brief → Outline → DesignSystem → PageSpecs → Review sequence; the model may return typed planning content or a clarification decision but never mutates runtime state directly.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, SQLAlchemy 2, SQLite, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-deck-planning-workflow-design.md`

## Global Constraints

- Develop on the current `feat/ppt` branch; do not create a worktree.
- SQLite, Workflow rows, Provider Attempts, cost records, events, and Artifacts remain the only durable facts.
- No automatic replay after a submitted DeepSeek request has an unknown outcome.
- Every model output is validated with strict Pydantic contracts before it can affect control flow or Artifact state.
- API keys and full sensitive request bodies never enter persisted events, errors, request fingerprints, or response summaries.
- Planning accepts normalized text/Markdown only; PDF/image parsing, Seedream, rendering, OCR, exports, React, and open-ended ReAct are out of scope.
- Follow repository milestone cadence: batched TDD and a small number of recoverable commits, followed by one whole-batch independent review and full verification.

## Review Focus

- Source text containing prompt-injection instructions must remain quoted untrusted data and cannot add tools, change Provider configuration, or bypass clarification.
- Valid JSON whose page count, page indexes, or Outline/PageSpec mapping conflicts with the Brief must be rejected as a charged schema failure.
- A disconnect after submission but before local response persistence must hold the reservation and block automatic retry.
- Duplicate, stale-version, type-invalid, and unknown-field clarification Resume payloads must not mutate the latest planning input.
- A targeted page revision containing duplicate or out-of-range page indexes must fail before invalidating any existing Artifact.

---

## File Structure

- `src/ppt_agent/domain/contracts.py`: planning contracts and cross-object validators.
- `src/ppt_agent/providers/text.py`: provider-independent request/result/error/pricing types and protocol.
- `src/ppt_agent/providers/deepseek.py`: one synchronous DeepSeek Responses API submission.
- `src/ppt_agent/operations/text_runner.py`: durable budget/Attempt/network/settlement orchestration.
- `src/ppt_agent/tools/clarification.py`: clarification and plan-review form construction/validation.
- `src/ppt_agent/workflows/planning_store.py`: planning-specific transactional queries and step/result persistence.
- `src/ppt_agent/workflows/deck_planning.py`: deterministic planning state machine and step handlers.
- `src/ppt_agent/skills/deck_planning/*.md`: versioned prompts with explicit untrusted-source boundaries.
- `src/ppt_agent/api/planning.py`: authenticated programmatic HTTP entry and Resume endpoints.
- `migrations/versions/0007_planning_runtime.py`: input/revision metadata needed for deterministic planning.

### Task 1: Planning contracts and deterministic clarification tools

**Files:**
- Modify: `src/ppt_agent/domain/contracts.py`
- Create: `src/ppt_agent/tools/__init__.py`
- Create: `src/ppt_agent/tools/clarification.py`
- Modify: `src/ppt_agent/runtime/repository.py`
- Test: `tests/unit/domain/test_planning_contracts.py`
- Test: `tests/unit/tools/test_clarification.py`
- Test: `tests/integration/runtime/test_interrupts.py`

**Interfaces:**
- Consumes: existing `ContractModel`, `SchemaEnvelope`, `PresentationBrief`, `WorkflowRepository.create_interrupt()` and `resume_interrupt()`.
- Produces: `DeckOutline`, `DesignSystem`, `PageSpec`, `PageSpecs`, `PlanningBundle`, `ClarificationDecision`, versioned form dictionaries, `RequestClarificationTool.create()`, `validate_form_response()`, and repository Resume validation.

- [ ] **Step 1: Write failing planning-contract tests**

Add tests that construct an 8-page Brief and assert:

```python
outline = DeckOutline(pages=[OutlinePage(page_index=i, objective=f"p{i}") for i in range(1, 9)])
pages = PageSpecs(
    brief_page_count=8,
    outline=outline,
    pages=[PageSpec(page_index=i, objective=f"p{i}", page_type="content") for i in range(1, 9)],
)
assert len(pages.pages) == 8
```

Also assert that duplicate indexes, gaps, count mismatch, objective mismatch, unknown fields, and page counts outside `1..50` raise `ValidationError`.

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_planning_contracts.py -q`

Expected: collection fails because `DeckOutline`, `OutlinePage`, `PageSpec`, and `PageSpecs` do not exist.

- [ ] **Step 3: Implement strict planning contracts**

Add these public shapes to `domain/contracts.py`:

```python
class OutlinePage(ContractModel):
    page_index: int = Field(ge=1, le=50)
    objective: str = Field(min_length=1, max_length=500)
    section: str | None = Field(default=None, max_length=200)

class DeckOutline(ContractModel):
    narrative: str = Field(min_length=1, max_length=2_000)
    pages: tuple[OutlinePage, ...]

class DesignSystem(ContractModel):
    color_palette: tuple[str, ...] = Field(min_length=2, max_length=8)
    typography: dict[str, str]
    visual_style: str = Field(min_length=1, max_length=1_000)
    image_style: str = Field(min_length=1, max_length=1_000)
    spacing_scale: tuple[int, ...] = Field(min_length=3, max_length=8)
    prohibited: tuple[str, ...] = ()

class PageSpec(ContractModel):
    page_index: int = Field(ge=1, le=50)
    objective: str = Field(min_length=1, max_length=500)
    page_type: Literal["cover", "section", "content", "data", "closing"]
    title: str | None = Field(default=None, max_length=200)
    body_blocks: tuple[str, ...] = ()
    layout_intent: str | None = Field(default=None, max_length=1_000)
    visual_requirements: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()

class PageSpecs(ContractModel):
    brief_page_count: int = Field(ge=1, le=50)
    outline: DeckOutline
    pages: tuple[PageSpec, ...]

class PlanningBundle(ContractModel):
    brief_artifact_id: str = Field(min_length=1)
    outline_artifact_id: str = Field(min_length=1)
    design_system_artifact_id: str = Field(min_length=1)
    page_spec_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=50)

class ClarificationQuestion(ContractModel):
    field: Literal["topic", "purpose", "audience", "language", "tone", "page_count", "budget_minor_units", "quality"]
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    input_type: Literal["single_select", "multi_select", "text", "number"]
    options: tuple[str, ...] = ()
    recommended_value: str | int | tuple[str, ...] | None = None
    required: bool = True

class BriefReady(ContractModel):
    kind: Literal["ready"] = "ready"
    brief: PresentationBrief

class ClarificationRequired(ContractModel):
    kind: Literal["clarification_required"] = "clarification_required"
    reason: str = Field(min_length=1, max_length=1_000)
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1, max_length=4)

ClarificationDecision: TypeAlias = Annotated[
    BriefReady | ClarificationRequired, Field(discriminator="kind")
]
```

Use `model_validator(mode="after")` on `DeckOutline` and a `PageSpecs` aggregate to enforce continuous indexes, exact Brief count, and matching page objectives.

- [ ] **Step 4: Write failing clarification-tool tests**

Cover one-to-four questions, Brief-field whitelist, duplicate target fields, select options, recommended-value type matching, and form response rejection:

```python
with pytest.raises(ClarificationValidationError, match="unknown answer"):
    validate_form_response(form, {"audience": "engineers", "api_key": "leak"})
```

Add an integration test showing an invalid Resume payload leaves Interrupt, Run, Step, and event count unchanged.

- [ ] **Step 5: Run clarification tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/tools/test_clarification.py tests/integration/runtime/test_interrupts.py -q`

Expected: import or assertion failure because the clarification form and Resume validator do not exist.

- [ ] **Step 6: Implement the form protocol and atomic Resume validation**

Implement:

```python
class ClarificationValidationError(ValueError):
    pass

class RequestClarificationTool:
    def create(
        self,
        *,
        repository: WorkflowRepository,
        run_id: str,
        step_id: str,
        questions: tuple[ClarificationQuestion, ...],
        reason: str,
        context: ClarificationContext,
    ) -> InterruptSnapshot: ...

def validate_form_response(
    form_schema: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]: ...

@dataclass(frozen=True, slots=True)
class ClarificationContext:
    input_refs: tuple[str, ...]
    prompt_version: str
    output_schema_name: str
    output_schema_version: int
    step_generation: int
```

Change `WorkflowRepository.resume_interrupt()` to validate the stored versioned form before mutating rows. Support `clarification_form/v1` and `plan_review/v1`; reject unknown schema names rather than accepting unvalidated payloads.

- [ ] **Step 7: Run Task 1 tests**

Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/domain/test_planning_contracts.py tests/unit/tools/test_clarification.py tests/integration/runtime/test_interrupts.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 8: Commit Task 1**

```powershell
git add src/ppt_agent/domain/contracts.py src/ppt_agent/tools src/ppt_agent/runtime/repository.py tests/unit/domain/test_planning_contracts.py tests/unit/tools/test_clarification.py tests/integration/runtime/test_interrupts.py
git commit -m "feat: add planning and clarification contracts"
```

### Task 2: DeepSeek structured provider and durable text operation runner

**Files:**
- Create: `src/ppt_agent/providers/text.py`
- Create: `src/ppt_agent/providers/deepseek.py`
- Create: `src/ppt_agent/operations/text_runner.py`
- Modify: `src/ppt_agent/operations/provider.py`
- Test: `tests/contract/providers/test_deepseek.py`
- Test: `tests/integration/providers/test_text_runner.py`

**Interfaces:**
- Consumes: `ProviderOperationService`, `PrepareAttempt`, `CostLedger`, `UsageInput`, and Task 1 Pydantic schemas.
- Produces: `StructuredTextProvider.complete()`, `DeepSeekProvider`, `TextOperationRunner.execute()`, `TextPricing`, normalized `ProviderCallError`, and charged-failure settlement.

- [ ] **Step 1: Write failing provider protocol and DeepSeek contract tests**

Use `httpx.MockTransport` to verify `POST {base_url}/responses`, Bearer authentication, `text.format.type=json_schema`, schema name/body, non-sensitive `user_id`, timeouts, and Usage parsing. Assert the request embeds untrusted source under an explicit data delimiter and never treats source text as system instructions.

Add parameterized status mapping tests for 400, 401, 402, 422, 429, 500, and 503. Add disconnect, empty output, `incomplete`, absent Usage, and malformed output tests.

- [ ] **Step 2: Run DeepSeek contract tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_deepseek.py -q`

Expected: import failure because `providers.text` and `providers.deepseek` do not exist.

- [ ] **Step 3: Implement provider-independent types and DeepSeek adapter**

Define:

```python
@dataclass(frozen=True, slots=True)
class StructuredTextRequest:
    model: str
    schema_name: str
    schema_version: int
    json_schema: dict[str, Any]
    system_prompt: str
    user_content: str
    max_output_tokens: int
    user_id: str

@dataclass(frozen=True, slots=True)
class TextUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0

@dataclass(frozen=True, slots=True)
class StructuredTextResult:
    response_id: str
    value: dict[str, Any]
    usage: TextUsage
    response_summary: dict[str, Any]

class StructuredTextProvider(Protocol):
    async def complete(self, request: StructuredTextRequest) -> StructuredTextResult: ...
```

`ProviderCallError` fields are `code`, `retryable`, `billing_known`, `confirmed_uncharged`, `usage`, and `safe_summary`. `DeepSeekProvider.complete()` sends exactly one request and never retries internally.

```python
@dataclass(frozen=True, slots=True)
class ProviderCallError(Exception):
    code: Literal[
        "invalid_request", "authentication_error", "insufficient_balance",
        "rate_limited", "temporary_unavailable", "content_rejected",
        "unknown_remote_outcome",
    ]
    retryable: bool
    billing_known: bool
    confirmed_uncharged: bool
    usage: TextUsage | None
    safe_summary: dict[str, Any]
```

- [ ] **Step 4: Write failing durable-runner and charged-failure tests**

Test four paths against real SQLite services:

1. Success settles actual Usage and selects the Attempt.
2. HTTP 200 with Usage but Pydantic failure settles cost and marks Attempt failed.
3. Confirmed uncharged failure releases reservation.
4. Disconnect after submission marks outcome unknown, changes reservation to `held_unknown`, and prevents another Attempt.

- [ ] **Step 5: Run runner tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/providers/test_text_runner.py -q`

Expected: import failure because `TextOperationRunner` and charged-failure settlement do not exist.

- [ ] **Step 6: Implement pricing, runner, and charged-failure settlement**

Use these public signatures:

```python
@dataclass(frozen=True, slots=True)
class TextPricing:
    version: str
    currency: str
    input_per_million: int
    output_per_million: int
    cached_input_per_million: int = 0
    safety_factor_millis: int = 1_200

class TextOperationRunner:
    async def execute(
        self,
        *,
        request: StructuredTextRequest,
        output_type: type[ContractModel],
        operation: PrepareAttempt,
        pricing: TextPricing,
    ) -> TextOperationResult: ...

@dataclass(frozen=True, slots=True)
class TextOperationResult:
    attempt_id: str
    operation_id: str
    output: ContractModel
    usage: TextUsage
```

Extend `ProviderOperationService` with `record_charged_failure(attempt_id, usage, error_code, response_summary)` that transitions the Attempt to failed and settles the reservation in one transaction. The runner commits submission intent before awaiting the Provider and hashes canonical request metadata without the API key or full user source.

- [ ] **Step 7: Run Task 2 tests**

Run: `& '.\.venv\Scripts\pytest.exe' tests/contract/providers/test_deepseek.py tests/integration/providers/test_text_runner.py tests/integration/providers/test_provider_operations.py tests/integration/ledger/test_cost_ledger.py -q`

Expected: all Task 2 tests pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add src/ppt_agent/providers/text.py src/ppt_agent/providers/deepseek.py src/ppt_agent/operations/text_runner.py src/ppt_agent/operations/provider.py tests/contract/providers/test_deepseek.py tests/integration/providers/test_text_runner.py tests/integration/providers/test_provider_operations.py
git commit -m "feat: add durable DeepSeek structured text calls"
```

### Task 3: Planning persistence, prompts, and deterministic workflow

**Files:**
- Create: `migrations/versions/0007_planning_runtime.py`
- Modify: `src/ppt_agent/storage/models.py`
- Create: `src/ppt_agent/workflows/__init__.py`
- Create: `src/ppt_agent/workflows/planning_store.py`
- Create: `src/ppt_agent/workflows/deck_planning.py`
- Create: `src/ppt_agent/skills/deck_planning/brief-v1.md`
- Create: `src/ppt_agent/skills/deck_planning/outline-v1.md`
- Create: `src/ppt_agent/skills/deck_planning/design-system-v1.md`
- Create: `src/ppt_agent/skills/deck_planning/page-specs-v1.md`
- Test: `tests/integration/storage/test_workflow_schema.py`
- Test: `tests/integration/workflows/test_deck_planning.py`

**Interfaces:**
- Consumes: Task 1 contracts/tools, Task 2 `TextOperationRunner`, `ArtifactStore`, and existing workflow state/event transitions.
- Produces: `PlanningStore`, `DeckPlanningWorkflow.create_run()`, `DeckPlanningWorkflow.execute()`, stable Prompt identities, planning Artifact dependency graph, and review/revision behavior.

- [ ] **Step 1: Write failing migration and planning-store tests**

Add `planning_inputs` with immutable input JSON, source Artifact refs, generation, feedback, revision scope, and created timestamp. Assert uniqueness on `(run_id, generation)` and FK cascade. Test `PlanningStore.current_input()`, `next_step()`, `record_step_completed()`, and stable idempotency keys.

The workflow-owned types are:

```python
PlanningStage = Literal[
    "build_brief", "build_outline", "build_design_system",
    "build_page_specs", "review_plan",
]

@dataclass(frozen=True, slots=True)
class PlanningStep:
    stage: PlanningStage
    step_id: str
    generation: int
    input_refs: tuple[str, ...]

class PlanRevisionRequest(ContractModel):
    action: Literal["revise"] = "revise"
    feedback: str = Field(min_length=1, max_length=4_000)
    scope: Literal["brief", "outline", "design", "pages", "auto"]
    page_indexes: tuple[int, ...] = ()
```

- [ ] **Step 2: Run storage tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/storage/test_workflow_schema.py tests/integration/workflows/test_deck_planning.py -q`

Expected: migration assertion or import failure because revision `0007_planning_runtime` and workflow classes do not exist.

- [ ] **Step 3: Implement migration and planning transactional store**

Create `PlanningInputRecord` and:

```python
class PlanningStore:
    def create_run(
        self, project_id: str, source_refs: tuple[str, ...], normalized_text: str
    ) -> RunSnapshot: ...

    def next_step(self, run_id: str) -> PlanningStep: ...

    def complete_step(
        self, step_id: str, output_refs: tuple[str, ...]
    ) -> StepSnapshot: ...

    def apply_revision(
        self, run_id: str, request: PlanRevisionRequest
    ) -> tuple[str, ...]: ...
```

All state transitions and core events occur in the same session transaction. Validate revision page indexes before calling Artifact invalidation.

- [ ] **Step 4: Write failing end-to-end fake-provider workflow tests**

Use a deterministic fake `StructuredTextProvider` and real SQLite/ArtifactStore to cover:

- Incomplete intent → one clarification Interrupt → valid Resume → Brief.
- Complete intent → Brief, Outline, DesignSystem, exactly N PageSpecs.
- Plan review approve → PlanningBundle and Run succeeded.
- Design revision invalidates DesignSystem/PageSpecs/Bundle but preserves Brief/Outline and old Artifact versions.
- Page revision rejects duplicates/out-of-range indexes before any invalidation.
- Crash after each committed step resumes at the next stage without repeating completed Provider operations.
- Source prompt injection cannot change tool list, model, budget, or output Schema.
- Review `cancel` persists cancel intent and produces no later Provider Operation or Artifact.

- [ ] **Step 5: Run workflow tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/workflows/test_deck_planning.py -q`

Expected: failures because prompts and `DeckPlanningWorkflow` are not implemented.

- [ ] **Step 6: Implement versioned prompt loader and step handlers**

Each prompt file contains its identity/version, expected Schema name, an explicit `<untrusted_source_content>` boundary, and no runtime secrets. Implement:

```python
class DeckPlanningWorkflow:
    async def create_run(
        self,
        *,
        project_id: str,
        normalized_text: str,
        source_refs: tuple[str, ...] = (),
    ) -> RunSnapshot: ...

    async def execute(self, run_id: str) -> None: ...

@dataclass(frozen=True, slots=True)
class PromptTemplate:
    identity: str
    version: int
    schema_name: str
    content: str

def load_prompt(identity: str, version: int) -> PromptTemplate: ...
```

`execute()` loops only across committed deterministic stages. Each stage compiles minimal context, calls `TextOperationRunner`, validates cross-object invariants, commits a JSON Artifact through all Artifact lifecycle phases, and atomically records Step outputs/events. It returns immediately after Interrupt, retry scheduling, recovery-required, cancellation, failure, or terminal success.

- [ ] **Step 7: Implement review approval and deterministic revision invalidation**

The review step creates `plan_review/v1`. On Resume:

- `approve` creates `planning/bundle`, writes Run result refs, and transitions Run to succeeded.
- `revise` validates scope/feedback/page indexes, creates a new PlanningInput, propagates Artifact invalidation, creates new Step generations from the earliest affected stage, and continues execution.
- `cancel` persists `cancel_requested` before returning to the Supervisor cancellation path.

- [ ] **Step 8: Run Task 3 tests**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/storage/test_workflow_schema.py tests/integration/workflows/test_deck_planning.py tests/integration/artifacts/test_artifact_store.py tests/integration/runtime/test_interrupts.py -q`

Expected: all Task 3 tests pass.

- [ ] **Step 9: Commit Task 3**

```powershell
git add migrations/versions/0007_planning_runtime.py src/ppt_agent/storage/models.py src/ppt_agent/workflows src/ppt_agent/skills/deck_planning tests/integration/storage/test_workflow_schema.py tests/integration/workflows/test_deck_planning.py
git commit -m "feat: implement deterministic deck planning workflow"
```

### Task 4: Authenticated application entry, live-test boundary, and milestone verification

**Files:**
- Create: `src/ppt_agent/api/planning.py`
- Modify: `src/ppt_agent/api/app.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `pyproject.toml`
- Modify: `AGENTS.md`
- Create: `tests/integration/api/test_planning.py`
- Create: `tests/live/providers/test_deepseek_live.py`

**Interfaces:**
- Consumes: `DeckPlanningWorkflow`, `ApplicationRuntime`, local Bearer security, `authorize_run`, and TaskSupervisor.
- Produces: authenticated planning create/read/resume routes, runtime workflow executor composition, and opt-in `live_provider` test marker.

- [ ] **Step 1: Write failing authenticated API tests**

Test:

- `POST /api/v1/projects/{project_id}/planning-runs` requires valid Host, Origin, Bearer token, normalized text size limits, and project authorization.
- `POST /api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume` validates ownership and form payload before scheduling the Run exactly once.
- `GET /api/v1/runs/{run_id}/planning` returns current version refs and status, never API keys or raw Provider responses.
- Browser disconnect does not cancel the scheduled planning task.

- [ ] **Step 2: Run API tests and verify RED**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_planning.py -q`

Expected: 404/import failure because planning routes are not registered.

- [ ] **Step 3: Implement routes and runtime composition**

Define:

```python
def planning_router(
    *,
    workflow: DeckPlanningWorkflow,
    supervisor: TaskSupervisor,
    authorize_project: ProjectAuthorizer,
    authorize_run: RunAuthorizer,
    security: LocalHTTPSecurity,
    input_limits: InputLimits,
) -> APIRouter: ...

ProjectAuthorizer = Callable[[str], Awaitable[None]]
```

Creation persists the Run before scheduling and returns `202`. Resume validates and persists before scheduling. Read routes project only durable state. Application readiness remains false until migrations, Artifact reconciliation, recovery, Provider configuration validation, and workflow composition all succeed.

- [ ] **Step 4: Add an explicitly opt-in live Provider test**

Register `live_provider` in `pyproject.toml`. The test skips unless `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` exist. It requests a tiny strict schema and asserts only structural validity and non-negative Usage, never exact prose.

- [ ] **Step 5: Run Task 4 and full verification**

Run:

```powershell
& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_planning.py tests/contract/providers/test_deepseek.py -q
& '.\.venv\Scripts\pytest.exe' -q
& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
git diff --check
```

Expected: API/contract tests pass; full suite passes with only environment-dependent link/live tests skipped; compileall and diff check produce no errors.

- [ ] **Step 6: Update repository handoff and commit Task 4**

Update `AGENTS.md` with the actual commit list, exact test count, completed planning capabilities, remaining Provider/rendering work, and any explicit review rulings.

```powershell
git add src/ppt_agent/api/planning.py src/ppt_agent/api/app.py src/ppt_agent/runtime/application.py pyproject.toml AGENTS.md tests/integration/api/test_planning.py tests/live/providers/test_deepseek_live.py
git commit -m "feat: expose runnable planning workflow"
```

## Final Review and Delivery

- Package one whole-batch review covering all commits after `73831d4`.
- Review specifically for billing uncertainty, crash boundaries, prompt injection, Resume authorization/schema validation, revision invalidation, and secret leakage.
- Fix every Critical/Important finding with a regression test; record any deliberate ruling and its cost.
- Re-run the full suite, `compileall`, `git diff --check`, and `git status --short`.
- Use `superpowers:finishing-a-development-branch` after the final verification.
