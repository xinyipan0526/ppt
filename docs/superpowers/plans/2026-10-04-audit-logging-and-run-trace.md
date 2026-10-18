# Audit Logging and Run Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist redacted structured audit logs, preserve safe planning failure details, expose bounded Run traces, and keep failed Runs visible in the React workspace.

**Architecture:** Configure a Workspace-owned rotating JSONL handler from the composition root. Emit only bounded structured context at runtime/provider boundaries. Build a read-only trace projection from existing SQLite Run/Event/Provider tables, then update the React workspace to retain terminal Run state and render the trace.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite, standard-library logging, React 19, TypeScript, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-04-audit-logging-and-run-trace-design.md`

## Global Constraints

- SQLite remains the source of truth for workflow state; logs and SSE remain projections.
- API keys, Authorization headers, raw prompts, source contents, and secret paths must never enter logs, events, or trace responses.
- Unknown remote outcomes remain `outcome_unknown` and are never automatically retried.
- Existing uncommitted user changes must be preserved.
- Use the existing src layout and current Workspace path/security boundaries.

## Review Focus

- A Provider HTTP 400 must remain visible after the Run becomes terminal; test safe error propagation.
- A failed Run must not disappear from the project workspace; test terminal selection and dismissal behavior.
- A trace request must be project-authorized and bounded; test cross-project denial and count limits.
- Log records containing credential-like keys or nested source data must be redacted; test nested dictionaries and exception text.
- Concurrent log writes and rotation must leave valid JSONL records; test multiple records and bounded file configuration.

---

### Task 1: Redacting Workspace JSONL logger

**Files:**
- Create: `src/ppt_agent/observability/logging.py`
- Test: `tests/unit/observability/test_logging.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Test: `tests/integration/runtime/test_application_logging.py`

**Interfaces:**
- Produces `configure_file_logging(workspace: Path, *, level: int = logging.INFO, max_bytes: int, backup_count: int) -> logging.Handler` and `shutdown_file_logging(handler) -> None`.
- The formatter accepts ordinary `logging` records plus bounded structured `extra` fields and emits one JSON object per line.

- [ ] Write failing unit tests for JSONL fields, nested redaction (`api_key`, `authorization`, `prompt`, `source_content`, secret paths), exception text, and handler rotation parameters.
- [ ] Run `pytest tests/unit/observability/test_logging.py -q` and verify the expected missing-module/API failures.
- [ ] Implement the formatter, redaction walk, bounded value conversion, and rotating handler using only standard-library logging and `RotatingFileHandler`.
- [ ] Run the unit tests and verify valid parseable JSONL and no sensitive values.
- [ ] Add an application-runtime integration test proving the configured Workspace receives a startup/shutdown record without changing readiness semantics.
- [ ] Run the focused integration test and the existing runtime tests.

### Task 2: Preserve safe Provider failure details

**Files:**
- Modify: `src/ppt_agent/providers/ark.py`
- Modify: `src/ppt_agent/operations/provider.py`
- Modify: `src/ppt_agent/operations/text_runner.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Test: `tests/contract/providers/test_ark_multimodal.py`
- Test: `tests/integration/providers/test_text_runner.py`
- Test: `tests/integration/api/test_planning.py`

**Interfaces:**
- `ProviderCallError.safe_summary` remains bounded and safe; explicit HTTP failures include `http_status` and a normalized provider error type when available, never raw response bodies.
- Step and Run error projections include `{code, provider_summary}` for known uncharged failures.

- [ ] Add a failing adapter test asserting HTTP 400 preserves only safe status/error metadata.
- [ ] Run the focused provider test and verify it fails because the current summary is incomplete/not persisted.
- [ ] Add a safe summary parameter to known-failure persistence and copy it into the Step/Run failure transition payloads.
- [ ] Emit structured log records at Provider submission, known failure, unknown outcome, Step failure, and Run terminal boundaries.
- [ ] Run provider, text-runner, and planning integration tests.

### Task 3: Bounded authorized Run trace API

**Files:**
- Modify: `src/ppt_agent/api/app.py` or create `src/ppt_agent/api/trace.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Create: `src/ppt_agent/events/trace.py`
- Test: `tests/integration/api/test_run_trace.py`

**Interfaces:**
- `GET /api/v1/runs/{run_id}/trace?event_limit=100&operation_limit=50` returns the Run snapshot, ordered events, and safe Provider operation/attempt summaries.
- Limits are clamped/rejected according to `InputLimits`; the existing `authorize_run` boundary is mandatory.

- [ ] Write failing integration tests for successful authorized access, cross-project denial, bounded events/operations, and absence of raw request/response fields.
- [ ] Run the focused tests and verify route/trace projection failures.
- [ ] Implement one bounded SQLite read projection using existing stores/models and safe field allowlists.
- [ ] Register the route in the composition root with existing authentication and ownership dependencies.
- [ ] Run the trace integration tests and existing event/API tests.

### Task 4: Frontend terminal failure and trace visibility

**Files:**
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Modify: `web/src/features/runs/RunStatus.tsx`
- Create: `web/src/features/runs/RunTrace.tsx`
- Modify: `web/src/api/contracts.ts`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`
- Test: `web/src/features/runs/RunTrace.test.tsx`

**Interfaces:**
- The workspace keeps a selected Run ID through terminal states until explicit dismissal.
- `RunTrace` reads `/api/v1/runs/{runId}/trace`, displays status, error code, timestamps, step transitions, and event types, and never displays secret/raw payload fields.

- [ ] Add failing React tests for a failed Run remaining visible with `invalid_request`, trace events rendering, and explicit dismissal.
- [ ] Run `npm test -- --run` from `web/` and verify the new tests fail for the current disappearing-terminal behavior.
- [ ] Implement terminal Run retention, failure banner, trace panel, and query invalidation without changing successful planning/review flow.
- [ ] Run focused frontend tests, then the complete frontend suite and typecheck.

### Task 5: Documentation and verification

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/api-tutorial.md`
- Test/verification: existing backend and frontend suites

- [ ] Document log location, rotation settings, redaction guarantees, trace endpoint, and SQLite fallback query for local audits.
- [ ] Run backend focused tests, frontend full tests, `compileall`, and `git diff --check`.
- [ ] Inspect generated JSONL and trace responses from the current Workspace without exposing the API key.
