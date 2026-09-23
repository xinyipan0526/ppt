# Infrastructure Runtime Closure Implementation Plan

> **Execution:** Native continuous implementation. The user explicitly replaced per-module gates with one merged infrastructure milestone. Use batched TDD, one final independent review, and a small number of recoverable commits.

**Goal:** Complete the persistence, state-machine, security, startup recovery, scheduling, backup, readiness, and shutdown infrastructure needed before business Deck workflows and real model adapters are added.

**Specs:** `docs/specs/02-workflow-runtime.md`, `docs/specs/03-provider-operations.md`, `docs/specs/05-artifact-lifecycle.md`, `docs/specs/06-event-stream.md`, `docs/specs/07-local-security-and-storage.md`.

**Non-goals:** Real DeepSeek/Qwen/Seedream calls, rendering, OCR, React UI, input parsers, and Deck-specific workflow handlers.

## Architecture

Startup is a deterministic pipeline: canonicalize/probe Workspace → hold its process lock → migrate → integrity-check → construct stores → reconcile Artifacts → normalize interrupted Run/Step/Page/Attempt state → start the in-process supervisor → expose readiness. Shutdown reverses ownership: stop admissions → persist shutdown intent → bounded drain → cancel local tasks → mark unfinished work `recovery_required` → stop maintenance → release database/lock.

SQLite remains the only scheduling fact source. `TaskSupervisor` tracks only tasks created by the current process; `RecoveryCoordinator` scans durable state and produces dispatch decisions. Provider Operations gain optional Run/Step/Page ownership columns so uncertain Attempts block only the correct execution scope.

## Combined Work

### 1. Security and runtime configuration

- Add `api/security.py`: high-entropy token lifecycle behind `TokenStore`, optional Keyring adapter, read-only Docker secret adapter, constant-time Bearer authentication, exact localhost Host/port validation, and Origin enforcement for unsafe methods.
- Update `create_app()` so every sensitive route and SSE requires application authentication before resource-level `authorize_run`; health remains minimal and readiness is explicit.
- Add API/unit tests for missing/wrong/query tokens, invalid Host, allowed IPv4/IPv6 localhost authorities, missing/foreign Origin, and authorization ordering.

### 2. Persistence maintenance and ownership migration

- Add migration `0006_runtime_recovery.py`: Provider Operation ownership (`run_id`, `step_id`, `page_id`), Run/Step retry timestamps and recovery metadata, plus indexes needed by startup scans.
- Extend models and operation requests without breaking existing callers.
- Add `storage/maintenance.py`: Alembic upgrade runner, `PRAGMA quick_check`/`foreign_key_check`, SQLite Online Backup API, manifest validation, safe restore preflight, and retention by moving expired backups to Workspace trash.
- Test upgrade from `0005`, ownership FKs, active-WAL backup consistency, corrupt backup rejection, and restore rollback behavior.

### 3. Durable recovery coordinator

- Extend repository APIs with atomic startup transitions and core events for Run/Step/Page restart, retry-due, shutdown recovery, and dispatch queries.
- Add `runtime/recovery.py`: move submitted/provider-running Attempts to `reconciling` before any Run dispatch; preserve `outcome_unknown`; normalize running entities to `recovery_required`; queue only scopes proven retry-safe; preserve waiting/cancel intent.
- Return immutable `RecoveryPlan` sets for queued, cancelling, blocked, and waiting runs. Recovery never performs provider network I/O; adapters later consume reconciling Attempts.
- Test mixed-run isolation, unknown Attempt blocking, due/not-due retries, waiting states, cancellation, idempotent repeated scans, and atomic events.

### 4. Supervisor, application composition, and maintenance loop

- Add `runtime/supervisor.py`: deduplicated run scheduling, global/project semaphores, provider semaphore context, cancellation, admission stop, bounded graceful shutdown, and exception-to-recovery persistence.
- Add `runtime/application.py`: `ApplicationRuntime` lifecycle with strict startup/cleanup ordering and injectable migration/reconciliation/executor boundaries.
- Add bounded event-retention maintenance task using server `recorded_at` cutoffs.
- Wire FastAPI lifespan/readiness to `ApplicationRuntime` without import-time filesystem or secret side effects.
- Integration-test startup ordering, readiness gating, queued recovery dispatch, executor isolation, cancellation, shutdown timeout recovery, lock contention, startup rollback, and restart idempotency.

## Verification and delivery

- Run combined infrastructure tests throughout implementation, then the full suite once the vertical runtime path is complete.
- Request one independent whole-batch security/recovery review; fix all Critical/Important findings with regression tests.
- Run `compileall`, `git diff --check`, and a final full suite.
- Commit recoverable checkpoints for schema/security, recovery/supervisor, and final application composition rather than one commit per internal module.

