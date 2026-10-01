# React Product UI Design

## Purpose

Deliver a local-first PPT Agent product that a personal user can start, open in a browser, configure with DeepSeek, Seedream, and Qwen API keys, and use through the complete durable backend workflow. The UI is a project workspace, not a demo wizard: it must remain useful after a deck has been generated and must expose recovery, cost, editing, versioning, and export capabilities without weakening backend safety guarantees.

## Product shape

The product is a React and TypeScript single-page application built with Vite and served from the same FastAPI origin as `/api/v1`. Development uses Node.js, but packaged and Docker users run only `ppt-agent serve`. The production frontend assets are included in the Python distribution. The CLI may open the browser automatically and provides an opt-out.

The application can always start in deterministic mode without provider keys. A first-run configuration screen accepts provider secrets and activates cloud mode once all required settings validate. The normal information architecture is a project workspace:

- Left rail: projects, source materials, and uploads.
- Top bar: current project, provider health, budget/cost, task state, and settings.
- Main area: requirements, clarification, plan review, production, or the completed slide gallery.
- Right inspector: current slide content, design instruction, local-edit prompt, quality report, and version history.
- Persistent task surface: progress, recovery actions, and exports.

Desktop authoring is the primary target. Narrow windows collapse the rails but do not attempt a full mobile authoring experience.

## Authentication and provider-secret boundary

React never receives or stores the long-lived local access token. FastAPI establishes an `HttpOnly`, `SameSite=Strict` local session cookie from a same-origin bootstrap response. Mutating requests retain strict Host and Origin validation and add CSRF validation. Bearer authentication remains supported for API and automation clients.

Provider keys exist in a password field only until submission. They must never enter a URL, browser storage, database row, SSE payload, log, trace, diagnostic archive, validation error, or response body. The backend prefers the operating-system credential store and falls back to an atomically written, permission-restricted Workspace secret file. Secret APIs return only configured state, storage backend, and a minimal masked suffix. Existing values cannot be read back; changing one requires submitting the replacement. Deletion is explicit and confirmed.

The server starts with deterministic providers when cloud configuration is incomplete. Complete, validated cloud configuration can be activated when no expensive operation is active. If work is active, the configuration is saved and marked pending rather than mutating providers underneath a run. Models, HTTPS endpoints, result-host allowlists, pricing, and pricing-version settings are editable under advanced settings; normal onboarding emphasizes the three keys and project budget.

## Project and planning workflow

The project list shows recent state, page count, budget, cost, and latest deck. A project accepts typed requirements and Markdown, PDF, PNG, JPEG, or WebP materials. Upload and parse state is visible; failed parsing has a safe error summary and retry action. Only ready materials can be selected for planning.

Clarification forms are rendered from the persisted server form contract. The frontend does not duplicate the model's intent-sufficiency decision. Refreshing or closing the browser leaves the run waiting at its checkpoint.

Plan review presents the Brief, DeckOutline, DesignSystem, and every PageSpec. Users can approve, cancel, revise the whole plan, or revise selected pages before starting expensive production. Each page exposes its delivery text and its page-level design instruction.

## Completed deck workspace

After production, the default main view is the complete slide gallery. Each card shows its page number, current generation, run status, and quality result. Selecting a slide opens a larger preview and the page inspector. Gallery and focused-slide layouts are both available. A failed page does not hide successful pages.

Page images and thumbnails are delivered by authorized, project-scoped endpoints. Workspace paths and arbitrary Artifact IDs are never exposed as a general download surface. Page history is immutable and supports visual comparison. Restoring an older page produces a new generation so history is never rewritten.

Every page inspector has three distinct entries:

1. Delivery text: title, body, data, citations, footer, and other deterministic text.
2. Design instruction: layout, composition, visual direction, illustration/background intent, and text safe area.
3. Local modification prompt: the natural-language instruction for the next edit.

Saving delivery text or design instructions creates a new page-spec version. Text and layout changes use deterministic rendering; visual resampling uses Seedream; ambiguous local edits use Qwen-assisted localization and surface a clarification card rather than guessing. Before submission the UI states which cloud capability may run and shows the available budget.

## Deck generations and global regeneration

Global regeneration is a prominent, guarded action. Its confirmation sheet shows the page count, current DesignSystem, current per-page text and design entries, expected provider call categories, and budget reservation impact. Confirmation creates a new immutable deck generation using the current global and page-level inputs. The previous generation remains selectable.

Progress and failure are page-scoped. Successfully generated pages are not repeated when retrying a failed page. A whole deck generation can be compared with or restored from an earlier generation; restoration creates another generation. Provider attempts with unknown remote outcomes are never silently resent.

## Status, recovery, cost, and export

SSE is the primary live projection and resumes from the last persisted event cursor. Bounded polling is the fallback. The UI distinguishes waiting for input, queued, running, safely retryable, unknown remote outcome, budget exhaustion, missing configuration, cancelled, and permanent failure. Known-uncharged retry schedules show their due time. Unknown outcomes require an explicit risk acknowledgement. Closing the browser never cancels work.

Exports support PNG, JPG, PNG ZIP, JPG ZIP, PDF, and image-only PPTX. PPTX is the primary action. The export surface shows page order, quality state, forced-export acknowledgement when required, and downloadable status. Downloads use the existing range-capable endpoint.

## Visual system and accessibility

The UI uses a creator-focused dark workspace with neutral charcoal surfaces so slide colors remain visually trustworthy. Restrained blue-violet accents identify primary actions. Slide previews use a light border and soft shadow. State is communicated with text and icons as well as color. Chinese is the default product language, with copy isolated for later internationalization.

Keyboard navigation supports moving between slides, closing overlays, saving, and undo/restore actions. Interactive controls have visible focus and accessible labels. The layout targets a 1280-pixel or wider authoring workspace and collapses secondary panels at smaller widths.

## Frontend architecture

The frontend uses React, TypeScript strict mode, Vite, React Router, TanStack Query, React Hook Form, Zod, Vitest, Testing Library, and Playwright. Server state belongs to TanStack Query; project, run, page, and workspace stage are represented in URLs; short-lived form input stays local. No general global-state library is introduced unless implementation demonstrates a concrete cross-cutting need.

TypeScript API contracts are generated or checked from the FastAPI OpenAPI document. The API client owns credentials, CSRF headers, error normalization, and resumable event handling. Feature folders own their routes, queries, mutations, and focused UI components.

## Required backend UI projections

The existing business workflows remain authoritative. The frontend requires these additional, narrow projections and commands:

- Provider-secret status, set, delete, validation, pending activation, and redacted configuration.
- Browser session bootstrap and CSRF initialization.
- Project summaries with recent runs and deck generations.
- Safe plan-detail projection for Brief, Outline, DesignSystem, and PageSpecs.
- Authorized page image, thumbnail, quality, specification, and generation-history reads.
- Page-spec revisions and restores.
- Whole-deck generation creation, regeneration, history, selection, and restore.
- Aggregated run, cost, and recovery actions needed by the workspace.
- Static SPA assets and history fallback outside `/api`.

These APIs return domain projections and never become an arbitrary Artifact download API.

## Verification and release acceptance

Frontend unit tests cover forms, state projections, API errors, and secret-field clearing. Security tests prove that provider keys do not appear in responses, browser storage, events, or logs and that cross-origin mutations and missing CSRF are rejected. Backend tests cover project ownership, version conflicts, deck-generation immutability, and artifact delivery authorization.

Playwright exercises a deterministic end-to-end path: first run, project creation, material upload, clarification, plan review, eight-page generation, page edit, global regeneration, and PPTX export. The release also runs the full Python suite, TypeScript checking, frontend unit tests, frontend production build, Python `compileall`, and `git diff --check`.

The final documentation covers local and Docker installation, browser setup, secure API-key configuration, deterministic mode, cloud provider requirements, editing, regeneration, recovery, budgets, and export. A user must be able to start the service, open the browser, configure the three provider keys, and operate the complete product without using raw HTTP commands.
