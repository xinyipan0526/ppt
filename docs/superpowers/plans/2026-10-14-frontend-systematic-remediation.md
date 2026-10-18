# Frontend Systematic Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every P0, P1, and P2 capability in the frontend analysis reachable, safe, typed, visually consistent, and covered by regression tests.

**Architecture:** Extend only the read models that the browser cannot derive safely, then keep all server state in TanStack Query and all confirmation state local to focused feature components. Introduce a small shared presentation layer for statuses, icons, confirmations, progress, money, and quality rather than replacing the existing application or adding a UI framework.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, React 19, TypeScript, TanStack Query 5, Vitest, Testing Library, Playwright, CSS custom properties.

**Spec:** `docs/superpowers/specs/2026-10-14-frontend-systematic-remediation-design.md`

## Global Constraints

- Preserve all existing uncommitted work and patch overlapping files incrementally; do not create a worktree.
- Do not trigger live Provider calls or paid smoke tests.
- Do not add canvas selection, manual masking, element-editable PPTX, multi-tenancy, mobile redesign, or inferred ETA.
- Do not add a UI framework, global state library, generated API client, or large icon dependency.
- Different currencies remain separate; the UI never invents exchange rates or budget figures.
- The browser projects persisted Workflow state and never implements a competing state machine.
- Because the worktree already contains overlapping user changes, do not create intermediate commits that would silently capture unrelated work; commit only after the final diff has been reviewed and scoped.

## Review Focus

- A Run with both `outcome_unknown` and still-reconciling Attempts must authorize only the unknown Attempt and keep local retry disabled until every uncertain Attempt is resolved.
- A project with multiple stages in one currency and another foreign currency must aggregate each currency independently without double-counting account totals.
- Selecting an old Deck then receiving a concurrent new generation must not regenerate from or restore against a stale expected generation.
- A page edit awaiting clarification must survive a browser refresh and resume only the persisted edit belonging to that page/project.
- A forced export retry must preserve format-specific quality options and must never send `forced=true` without a non-empty acknowledgement.

---

### Task 1: Safe read models and exact browser contracts

**Files:**
- Modify: `src/ppt_agent/api/operations.py`
- Modify: `src/ppt_agent/api/workspace.py`
- Modify: `src/ppt_agent/workflows/page_editing.py`
- Modify: `web/src/api/contracts.ts`
- Create: `tests/integration/api/test_operations_api.py`
- Test: `tests/integration/api/test_product_workspace_api.py`

**Interfaces:**
- Produces: `ProjectCosts` with `currencies: CostCurrencySummary[]`, where each summary contains `currency`, `hard_limit`, `settled`, `active_reserved`, `held_unknown`, and `available` as integer minor units or `null` when no hard limit exists.
- Produces: `RunSnapshot.run.retry_due_at: string | null` sourced only from persisted retry metadata.
- Produces: `PageDetail.pending_edit: PageEditSummary | null`, with `id`, `status`, `kind`, `clarification_question`, and safe normalized `target_box`.
- Consumes: existing BudgetAccount, Reservation, Run recovery metadata, and PageEdit records; no new state machine states.

- [ ] **Step 1: Add failing API tests for cost aggregation, retry due time, and pending edit projection.**

Assert literal responses for two stages sharing CNY, one USD account, active and held reservations, no-hard-limit availability, a persisted retry due timestamp, and a page edit in `awaiting_clarification`.

- [ ] **Step 2: Run the focused Python tests and verify RED.**

Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/api/test_operations_api.py tests/integration/api/test_product_workspace_api.py -q --basetemp='.cache/pytest-frontend-read-model-red'`

Expected: failures because the new fields/projections do not exist.

- [ ] **Step 3: Implement the minimal read-model extensions.**

Add pure aggregation helpers in `operations.py`; expose persisted retry evidence without deriving dates; add `PageEditingWorkflow.pending_for_page(page_id: str) -> PageEditSnapshot | None`; serialize only safe edit-plan fields in `workspace.py`.

- [ ] **Step 4: Add exact TypeScript interfaces matching the responses.**

Define `ProjectCosts`, `CostCurrencySummary`, `PageEditSummary`, `NormalizedBox`, typed planning contracts, and typed quality summaries in `contracts.ts` without loosening existing fields to `any`.

- [ ] **Step 5: Run the focused Python tests and verify GREEN.**

Run the Step 2 command; expect all selected tests to pass.

### Task 2: Shared status, icon, money, quality, and confirmation presentation

**Files:**
- Create: `web/src/components/Icon.tsx`
- Create: `web/src/components/ConfirmDialog.tsx`
- Create: `web/src/components/ProgressBar.tsx`
- Create: `web/src/features/runs/statusPresentation.ts`
- Create: `web/src/features/projects/CostSummary.tsx`
- Create: `web/src/features/deck/QualitySummary.tsx`
- Modify: `web/src/styles/tokens.css`
- Modify: `web/src/styles/global.css`
- Test: `web/src/components/ConfirmDialog.test.tsx`
- Test: `web/src/features/runs/statusPresentation.test.ts`
- Test: `web/src/features/projects/CostSummary.test.tsx`
- Test: `web/src/features/deck/QualitySummary.test.tsx`

**Interfaces:**
- Produces: `<Icon name: IconName />`, `<ConfirmDialog open title confirmLabel danger busy acknowledgement? />`, `<ProgressBar value max label />`.
- Produces: `getStatusPresentation(status: string): { label: string; tone: StatusTone; icon: IconName; guidance?: string }`.
- Produces: `<CostSummary costs: ProjectCosts />` and `<QualitySummary quality: QualityReport | null compact?: boolean />`.

- [ ] **Step 1: Write failing component tests.**

Cover keyboard-safe confirmation with required acknowledgement, all formal Run statuses in Chinese, separate CNY/USD amounts with no invented budget, and pass/warn/fail/unknown quality summaries.

- [ ] **Step 2: Run the focused Vitest files and verify RED.**

Run: `npm test -- ConfirmDialog.test.tsx statusPresentation.test.ts CostSummary.test.tsx QualitySummary.test.tsx`

Expected: failures because the components and mapper do not exist.

- [ ] **Step 3: Implement the shared presentation layer.**

Reuse the existing `Dialog`; inline accessible SVG paths; format integer minor units deterministically; map only known quality structures and provide a bounded fallback summary.

- [ ] **Step 4: Replace tokens and add semantic component classes.**

Use a restrained blue-violet accent, complete status tones, spacing/type scales, focus rings, reduced-motion behavior, progress, summary, action-row, icon-button, and skeleton classes.

- [ ] **Step 5: Run focused tests and `npm run typecheck`; verify GREEN.**

### Task 3: Operation recovery, status guidance, costs, and real progress

**Files:**
- Modify: `web/src/features/runs/RunTrace.tsx`
- Modify: `web/src/features/runs/RecoveryActions.tsx`
- Modify: `web/src/features/runs/RunStatus.tsx`
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Test: `web/src/features/runs/RunTrace.test.tsx`
- Test: `web/src/features/runs/RecoveryActions.test.tsx`
- Test: `web/src/features/runs/RunStatus.test.tsx`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`

**Interfaces:**
- Consumes: Task 1 `ProjectCosts`/`retry_due_at`; Task 2 status presentation, cost summary, confirm dialog, and progress bar.
- Produces: `RunTrace({ runId, projectId?, onRecoveryChange? })` that authorizes only an `outcome_unknown` Attempt with its exact ID.
- Produces: `RecoveryActions({ runId, unresolvedRemoteCount, ... })` that blocks local recovery while the count is nonzero.

- [ ] **Step 1: Add failing tests for the complete recovery path and status guidance.**

Assert the exact operation authorization body, no authorization button for reconciling/cancel-requested Attempts, local retry gating, Chinese status labels, persisted due time, cost invalidation, and determinate/indeterminate progress.

- [ ] **Step 2: Run the four focused tests and verify RED.**

- [ ] **Step 3: Implement operation authorization and recovery query invalidation.**

Keep acknowledgement state per Attempt, surface safe conflict guidance, and invalidate trace/snapshot/cost keys only after success.

- [ ] **Step 4: Integrate project costs and status/progress presentation.**

Fetch `/projects/{projectId}/costs` in the workspace header; replace raw badges and inline progress text with Task 2 primitives.

- [ ] **Step 5: Run focused tests and verify GREEN.**

### Task 4: Deck generations, complete plan review, and page quality

**Files:**
- Create: `web/src/features/deck/DeckGenerationPicker.tsx`
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Modify: `web/src/features/deck/DeckGallery.tsx`
- Modify: `web/src/features/deck/PageInspector.tsx`
- Modify: `web/src/features/planning/PlanReview.tsx`
- Modify: `web/src/features/planning/PageSpecCard.tsx`
- Test: `web/src/features/deck/DeckGenerationPicker.test.tsx`
- Test: `web/src/features/deck/PageInspector.test.tsx`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`
- Create: `web/src/features/planning/PlanReview.test.tsx`

**Interfaces:**
- Consumes: typed contracts and shared confirm/status/quality components from Tasks 1–2.
- Produces: `<DeckGenerationPicker projectId generations selectedId onSelect onRestored />` with expected-generation restore protection.
- Produces: full `PlanReview` sections for Brief, Outline, DesignSystem, and pages.

- [ ] **Step 1: Write failing tests for version selection/restore, full planning content, and quality detail.**

Include concurrent-generation conflict behavior, historical-version labelling, plan-load failure disabling approval, and quality-card/quality-tab rendering.

- [ ] **Step 2: Run the focused tests and verify RED.**

- [ ] **Step 3: Implement Deck selection and restore without stale actions.**

Hold only selected generation ID locally; derive the selected object from fresh query data; disable global regeneration for non-current generations; select the returned restored generation.

- [ ] **Step 4: Implement typed plan sections and quality presentation.**

Render only user-facing fields in stable order; preserve unknown forward-compatible fields at the API boundary without dumping them into the DOM.

- [ ] **Step 5: Run focused tests and verify GREEN.**

### Task 5: Visual edits, persisted clarification, and unified paid confirmations

**Files:**
- Modify: `web/src/features/deck/PageInspector.tsx`
- Modify: `web/src/features/deck/GlobalRegenerateDialog.tsx`
- Test: `web/src/features/deck/PageInspector.test.tsx`
- Test: `web/src/features/deck/GlobalRegenerateDialog.test.tsx`

**Interfaces:**
- Consumes: `PageDetail.pending_edit`, `ConfirmDialog`, and existing edit/resume endpoints.
- Produces: separate local and visual edit actions; visual requests set `resample_visual: true`; resume sends the persisted edit ID and accepted clarification fields.

- [ ] **Step 1: Add failing tests for local edit, visual edit confirmation, refresh-safe clarification, resume, and custom single-page regeneration confirmation.**

- [ ] **Step 2: Run `PageInspector.test.tsx` and verify RED.**

- [ ] **Step 3: Implement the two edit modes and persisted clarification UI.**

Do not expose manual target-box drawing; accept a server-projected normalized target only when present, otherwise resume with clarification text.

- [ ] **Step 4: Replace `window.confirm` and textual/emoji action glyphs with shared confirmation and icons.**

- [ ] **Step 5: Run focused tests and verify GREEN.**

### Task 6: Forced export and destructive project/material actions

**Files:**
- Modify: `web/src/features/exports/ExportPanel.tsx`
- Modify: `web/src/features/projects/ProjectList.tsx`
- Modify: `web/src/features/materials/MaterialPanel.tsx`
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Test: `web/src/features/exports/ExportPanel.test.tsx`
- Test: `web/src/features/projects/ProjectList.test.tsx`
- Test: `web/src/features/materials/MaterialPanel.test.tsx`

**Interfaces:**
- Consumes: shared `ConfirmDialog` and icons.
- Produces: forced retry request preserving `format`, `jpeg_quality` or `pdf_dpi`, plus `forced: true` and non-empty `risk_acknowledgement`.
- Produces: confirmed DELETE actions with non-optimistic cache updates.

- [ ] **Step 1: Write failing tests for export-risk acknowledgement and both delete flows.**

Verify cancellation sends nothing, failed mutations keep dialogs open, material selection is cleared only after success, and the forced request preserves format-specific options.

- [ ] **Step 2: Run the three focused tests and verify RED.**

- [ ] **Step 3: Implement forced export retry from the original request snapshot.**

Recognize only the stable `export_not_ready` API error code/status; do not offer force for arbitrary failures.

- [ ] **Step 4: Implement project and material deletion with shared dialogs.**

- [ ] **Step 5: Run focused tests and verify GREEN.**

### Task 7: Onboarding and systematic style migration

**Files:**
- Modify: `web/src/app/App.tsx`
- Modify: `web/src/features/projects/ProjectList.tsx`
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Modify: `web/src/features/materials/MaterialPanel.tsx`
- Modify: `web/src/features/planning/ClarificationForm.tsx`
- Modify: `web/src/features/planning/PageSpecCard.tsx`
- Modify: `web/src/features/planning/PlanReview.tsx`
- Modify: `web/src/features/planning/PlanningInput.tsx`
- Modify: `web/src/features/deck/DeckGallery.tsx`
- Modify: `web/src/features/deck/PageHistory.tsx`
- Modify: `web/src/features/deck/PageInspector.tsx`
- Modify: `web/src/features/exports/ExportPanel.tsx`
- Modify: `web/src/features/runs/RecoveryActions.tsx`
- Modify: `web/src/features/runs/RunStatus.tsx`
- Modify: `web/src/features/runs/RunTrace.tsx`
- Modify: `web/src/features/settings/ProviderSetup.tsx`
- Test: `web/src/app/App.test.tsx`
- Test: affected existing component tests

**Interfaces:**
- Consumes: semantic CSS classes, icons, status mapper, progress, and dialogs from Task 2.
- Produces: no new data interfaces; this task removes static inline layout styles and unifies product language/visual rhythm.

- [ ] **Step 1: Add failing onboarding and accessibility assertions.**

Cover the empty-workspace settings path, deterministic-mode non-blocking copy, text alongside every semantic icon, keyboard-visible controls, and page skeletons during generation.

- [ ] **Step 2: Run affected tests and verify RED.**

- [ ] **Step 3: Migrate report-listed static inline styles to semantic classes.**

Keep only runtime-computed values such as progress width; use no source-text grep tests as the behavioral assertions.

- [ ] **Step 4: Finish onboarding, icon/text pairing, badge tones, skeletons, and reduced-motion behavior.**

- [ ] **Step 5: Run frontend unit tests, typecheck, and build; verify GREEN.**

Run:

```powershell
npm test -- --run
npm run typecheck
npm run build
```

### Task 8: Deterministic product flow and full verification

**Files:**
- Modify: `web/e2e/product-flow.spec.ts`
- Modify: `web/e2e/secret-safety.spec.ts` only if new sensitive forms require coverage
- Modify: `docs/ui-guide.md`

**Interfaces:**
- Consumes: completed UI and deterministic backend fixtures.
- Produces: end-to-end proof of onboarding, operation recovery, version restore, visual edit clarification, forced export, and delete behavior without a live Provider.

- [ ] **Step 1: Extend Playwright fixtures and assertions for the new user-visible flows.**

- [ ] **Step 2: Run Playwright and fix only failures attributable to this batch.**

Run: `npm run e2e`

- [ ] **Step 3: Update the UI guide with recovery, costs, versions, visual edits, forced export, and deletion.**

- [ ] **Step 4: Run fresh full verification.**

Run:

```powershell
npm test -- --run
npm run typecheck
npm run build
& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-frontend-remediation-final'
& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
git diff --check
git status --short
```

- [ ] **Step 5: Review the complete diff against every spec completion criterion.**

Separate pre-existing worktree changes from this batch in the handoff; do not stage or commit unrelated files.
