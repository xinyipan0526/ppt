# Complete Backend PPT Agent Design

## Status

Approved on 2026-09-29. This specification completes the first-release backend while deliberately leaving the React UI and second-release editing capabilities out of scope.

## Outcome

The repository will expose a production-oriented, local-first PPT Agent service that can take text, Markdown, PDF, and image materials through planning, image generation, deterministic rendering, validation, editing, and export. The same REST and SSE workflow must operate with real DeepSeek, Seedream, and Qwen cloud APIs or with explicitly configured deterministic providers that require no API keys.

The completed backend must generate PNG/JPG pages, ZIP archives, PDFs, and image-based PPTX files. It must remain cost-controlled, restart-safe, observable, and usable without a React frontend.

## Scope

This milestone includes:

- project lifecycle APIs;
- the already implemented material-ingestion and planning flows;
- Seedream generation and reference-image editing;
- Qwen vision understanding and Qwen OCR;
- deterministic HTML/CSS/SVG composition in a fixed Chromium environment;
- deterministic and vision-assisted quality checks;
- production, page regeneration, and natural-language page-edit workflows;
- authenticated downloads and PNG, JPG, ZIP, PDF, and image-based PPTX export;
- native CLI startup, diagnostics, migrations, redacted effective configuration, a backend container image, and operator documentation;
- deterministic end-to-end operation without API keys and opt-in live tests for all real providers.

This milestone excludes React, element-editable PPTX, arbitrary user masks or canvas scribbles, local vision models, distributed workers, multi-user remote deployment, and an offline evaluation platform.

## Architecture

The existing explicit state machine, SQLite checkpoints, TaskSupervisor, Operation/Attempt protocol, cost ledger, Artifact lifecycle, and persisted event stream remain authoritative. New functionality extends these protocols rather than creating a synchronous shortcut.

```text
source materials
  -> deck_planning/v1
  -> approved planning bundle
  -> deck_production/v1
       -> production specification
       -> per-page visual preparation
       -> Seedream generation or deterministic visual
       -> deterministic page rendering
       -> deterministic and Qwen/OCR validation
       -> page Artifact commit
       -> deck validation
       -> export generation
  -> immutable downloadable export Artifacts
```

Production pages may execute concurrently within global, project, and Provider limits. Client disconnects never own task lifetime. Browser rendering and CPU-heavy export operations run behind bounded asynchronous adapters and have explicit shutdown limits.

## Versioned Contracts

Existing planning contracts are not mutated in place. The backend adds strict, versioned contracts:

- `ProductionSpecV1` and `ProductionPageSpecV1`;
- `VisualRequestV1`;
- `RenderManifestV1` and `DOMLayoutReportV1`;
- `VisionAnalysisV1`, `OCRReportV1`, and `QualityReportV1`;
- `PageEditRequestV1` and `PageEditPlanV1`;
- `ExportManifestV1`.

The logical canvas is always 1920 by 1080. A page explicitly selects `hybrid`, `deterministic`, or `full_raster`. Contracts carry safe areas, constrained layout tokens, structured text/table/chart/page-number blocks, typography limits, visual regions, source provenance, and version identity. Models cannot supply executable HTML, CSS, or JavaScript.

Planning output is deterministically compiled into a production specification. Missing layout detail is filled by versioned, deterministic layout rules rather than another unconstrained model response.

## Provider Boundary

The capability layer exposes `ImageGenerationProvider`, `VisionUnderstandingProvider`, and `OCRProvider`. Workflows depend only on these protocols and versioned capability declarations.

### Seedream

The Volcengine Ark adapter supports text-to-image, reference-image generation, and reference-image instruction editing through the official image generation endpoint. It disables watermarking, web search, and group generation. Model, region, base URL, request size, pricing version, and limits are configured rather than inferred from a moving alias.

Requests are locally checked against the declared maximum image count, bytes, pixels, formats, and aspect ratios. Local images are sent as bounded Base64 data URLs. Returned URL or Base64 content is immediately validated and committed as an Artifact.

Provider result URLs are ephemeral and untrusted. Downloads require HTTPS, an explicit Provider-host allowlist, disabled or revalidated redirects, bounded time and bytes, media sniffing, decode limits, and canonical Workspace containment. Result URLs and credentials never enter public events or ordinary logs.

### Qwen vision and OCR

The Alibaba Cloud Model Studio adapter uses the OpenAI-compatible multimodal endpoint for structured vision analysis and the supported Qwen OCR capability for positional text recognition. Model, workspace-specific base URL, region, price, token ceiling, timeout, and input limits are configured. API keys must match their configured region.

Images are sent as bounded Base64 data URLs. Vision responses are validated against strict local contracts. OCR output is normalized into text spans and normalized boxes before persistence.

### Durable operations and billing

Every real cloud request performs atomic budget reservation, creates an Operation and prepared Attempt, persists submission intent, performs one submission, persists the validated structured or binary result, settles Usage, and selects the Attempt.

Timeouts and transport failures after submission are `outcome_unknown` unless there is positive evidence that the request was not accepted or billed. Unknown results are never automatically retried and their reservations remain held. Success responses required for Artifact recovery are durably recorded before the operation is selected.

Pricing is explicit and versioned. When precise Provider pricing cannot be represented, users configure a conservative per-call amount; the service never invents precise cost data.

### Deterministic providers

`provider_mode = "deterministic"` supplies stable image, vision, OCR, and structured-text results derived from request fingerprints. Deterministic providers still use Operations, Attempts, budget reservations, Usage, Artifacts, and recovery. Production mode without required keys fails startup; a real-provider failure never silently falls back to deterministic output.

## Rendering

The renderer consumes only a validated `RenderManifestV1`. Repository-owned templates generate HTML/CSS/SVG. Network access, arbitrary scripts, external URLs, and reads outside the isolated render directory are forbidden.

Chromium uses a fixed viewport, device scale factor, launch flags, and approved font set. Rendering waits for fonts and records a DOM layout report before capture. A `FontRegistry` discovers approved Chinese, Latin, and symbol fallback chains, hashes actual files, and contributes its fingerprint plus Chromium and renderer versions to the cache identity. Production startup fails if no approved Chinese fallback exists.

Overflow handling is deterministic: natural wrapping, shrink above the declared minimum size, then an allowed fallback layout. Failure after those steps is `layout_overflow`. Titles, numbers, and quotations are not silently truncated.

The canonical page Artifact is lossless PNG. JPG is a derived export. Each completed page records visual assets, render manifest, DOM report, page image, thumbnail, and quality report with `derived_from`, `composed_from`, and `validates` edges.

## Validation

Local validation runs first and checks dimensions, format, safe-area containment, overlap, overflow, minimum font size, contrast, blank or nearly uniform output, Artifact integrity, page numbering, and page count.

Qwen vision and OCR then check delivery-critical text, numbers, quotations, generated-text contamination, visual intrusion into safe areas, and edit intent. OCR evidence does not override structured source text by itself. A mismatch is interpreted alongside the DOM report to distinguish recognition error from rendering error.

Automatic remediation may reflow deterministic content, apply an approved fallback layout, crop or mask a visual, or resume an interrupted local stage. It may not silently create another paid Provider request.

## Production, Regeneration, and Editing

`deck_production/v1` references one approved planning bundle. Run creation atomically revalidates project ownership, Artifact availability, approval status, material bindings, and budget configuration.

Each page uses the existing formal Page states and the stages `visual_generation`, `rendering`, and `validation`. Fine-grained progress lives in steps, events, and Artifacts rather than new transient enum values.

Page regeneration creates a new immutable page generation. Callers explicitly choose whether to reuse the existing visual or resample it. Only the selected page and dependent deck exports become stale.

The page-edit sub-workflow converts natural language into one of `content_edit`, `style_edit`, `visual_edit`, `full_page_regeneration`, or `ambiguous`:

- content and style edits update structured page data and render without Seedream;
- visual edits use Qwen to locate a normalized target region, create a bounded crop or overlay description, and invoke Seedream reference-image editing;
- full-page regeneration creates a new generation;
- ambiguity creates a versioned Interrupt rather than guessing.

The first release does not accept arbitrary masks. Every revision retains source Run, prior page version, cost, and Artifact dependencies.

## Export

Export reads only available final page images in an explicit order. Supported products are a single PNG or JPG, an all-page PNG or JPG ZIP, a 16:9 image PDF, and an image-based PPTX with one full-slide image per slide.

The `ExportManifestV1` fixes page order, content hashes, format, dimensions, quality parameters, source Run, and exporter version. Identical manifests may reuse an available cached Artifact. ZIP entries have server-generated safe names. Download uses authorized Artifact IDs and streaming; callers never provide filesystem paths.

Ordinary export is refused if a required page is stale, invalid, missing, or depends on an unresolved unknown Provider outcome. Explicit forced export records the acknowledged risk in a durable event.

## Persistence and Recovery

New migrations persist Provider binary/vision results, production-to-planning relationships, page generations, current page references, quality summaries, exports, recoverable result-download state, edit lineage, and complete Material attempt history. Data already represented by Artifact metadata or dependency edges is not duplicated.

Recovery rules are evidence-based:

- submitted unknown Provider work remains manual;
- a durable successful Provider response with an unfinished download resumes validation and Artifact commit without another submission;
- a committed visual resumes rendering;
- a committed page image resumes validation;
- completed pages resume export;
- interrupted Chromium and local export work is safe to rerun after child cleanup.

Artifact maintenance obtains the Workspace maintenance lock. Artifact references gain database-level project-consistency enforcement. Known retryable failures use persisted due times and an explicit scheduler policy; unknown results remain excluded.

## HTTP Surface

The backend adds project CRUD, redacted Provider capability/status and connection-test endpoints, production creation/query/resume/cancel, page list/detail/regeneration/edit, export creation/status/download, and Run cost/recovery endpoints. Existing materials, planning, event, and SSE APIs remain compatible.

All routes use the existing Host, Origin, bearer-token, project, and Run authorization boundaries. Input sizes and concurrency are bounded. Errors expose stable codes and safe summaries, never secrets, prompts, full Provider bodies, source content, or local paths.

## Configuration and Operations

The package exposes:

```text
ppt-agent serve --config <path>
ppt-agent doctor --config <path>
ppt-agent migrate --config <path>
```

Precedence is CLI, environment, TOML, then safe defaults. API keys are loaded only from environment, system Keyring, or Docker secret files. Effective configuration endpoints and logs are redacted.

Configuration covers Workspace and database paths, bind address, authentication, Provider mode and endpoints, models, region, pricing, budgets, concurrency, input/render/export limits, Chromium, fonts, Artifact retention, maintenance, and backups.

`doctor` checks Workspace capabilities, migrations, font availability, Chromium, Provider configuration completeness, secret presence without secret display, free space, and output readiness.

Delivery includes `.env.example`, `config/ppt-agent.example.toml`, native and container startup assets, and dedicated quickstart, configuration, Provider setup, and API tutorial documentation.

## Security

The existing local threat model remains. New code additionally prevents Provider-result SSRF, blocks Chromium network access, disallows arbitrary template code, validates all decoded images, bounds Base64 and JSON payloads, isolates temporary render directories, sanitizes download names, and redacts external error bodies.

Provider connections are outbound only to explicitly configured HTTPS endpoints. Non-HTTPS endpoints are accepted only for deterministic test fixtures under an explicit non-production setting.

## Verification

Unit, integration, contract, recovery, security, and deterministic end-to-end tests cover:

- strict contracts and capability negotiation;
- Seedream and Qwen request/response/error mapping;
- budget races, known failures, unknown results, and crash boundaries;
- result URL SSRF, redirects, oversized or malformed images;
- Chromium isolation, font fallback, layout overflow, and process shutdown;
- page-level invalidation and no-charge deterministic edits;
- PNG/JPG/ZIP/PDF/PPTX validity, order, dimensions, and cache identity;
- authorization, cancellation, recovery, SSE, downloads, configuration, CLI, and diagnostics;
- a no-key input-to-PPTX deterministic workflow.

Live DeepSeek, Seedream generation/edit, Qwen vision, and Qwen OCR tests remain opt-in and require complete explicit configuration.

Completion requires a clean independent whole-batch review, resolution of all Critical and Important findings with regression tests, a passing full test suite, `compileall`, `git diff --check`, a clean worktree, and documentation sufficient for a new operator to create the first deck from an empty Workspace.
