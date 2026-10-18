# Batch A final review residuals

**Goal:** Close the five load-bearing residuals from the Batch A scoped whole-plan re-review without changing deferred budget/price work or `outcome_unknown` safety.

## Task 1: Complete paid-action gating

Keep all existing project data/read-only controls visible while `setup_required=true`, but disable every action that can invoke a cloud Provider: planning start, clarification resume, plan revision/approval paths that call models, production start, global regeneration, page regeneration, visual edit, and already-open paid dialogs. Thread a typed `disabled/setupRequired` capability through the relevant components. Add component regressions for existing material/run/deck visibility and every paid entry point.

## Task 2: Preserve useful safe tracebacks and CLI logging ownership

Format redacted exceptions so bounded JSONL retains the final exception type, endpoint/user-code tail frames, and cause chain rather than only the traceback prefix. Never include raw messages, secrets, payloads, or private paths. Make Alembic logging configuration preserve the CLI Workspace file handler and prevent full tracebacks on generic stderr. Add real nested ASGI and real migration-failure CLI regressions.

## Task 3: Make explicit export retry eventually execute

On same-ID terminal requeue, immediately seed/update TanStack Query data to queued and resume polling. In ExportSupervisor, if schedule collides with the old task teardown, guarantee eventual rescheduling by rechecking durable queued state after task ownership is released; preserve single execution, startup terminal non-retry, attempt history, manifest/page revalidation, and shutdown ownership. Add deterministic race tests plus UI queued→available polling coverage.

## Verification

Run task-focused RED/GREEN, related backend/frontend suites, full Python, full React with one worker, typecheck/build/static SHA synchronization, compileall, and diff-check. Chromium remains a separate release-environment requirement if this Windows harness still cannot return a final exit summary.
