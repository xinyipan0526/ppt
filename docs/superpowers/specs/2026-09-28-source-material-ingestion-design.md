# Source Material Ingestion Design

**Date:** 2026-09-28  
**Status:** Approved in conversation; awaiting written-spec review  
**Scope:** Secure ingestion and parsing of Markdown, PDF, PNG, JPEG, and WebP source materials

## 1. Objective

Add a durable, local-first path that accepts user materials, preserves the original bytes, parses them under explicit resource limits, and exposes a versioned normalized representation to the existing planning workflow.

Success means that:

- every accepted upload is committed through the existing Artifact lifecycle before parsing starts;
- parsing can be retried safely after process failure without losing or silently replacing source bytes;
- all parsed content retains project ownership and source-location metadata;
- Markdown, PDF, and image limits are enforced by the implementation rather than existing only as configuration fields;
- planning runs consume server-validated ready materials instead of trusting arbitrary client-supplied Artifact references; and
- source text is always labeled and transported as untrusted data.

OCR, remote vision analysis, image generation, rendering, export, and React UI are outside this subproject.

## 2. Chosen Architecture

Use an Artifact-first pipeline with a persistent material state machine.

```text
authenticated upload
  -> stream and enforce byte limits
  -> sniff and validate supported media type
  -> commit immutable source Artifact
  -> create SourceMaterial(uploaded)
  -> schedule local parse
  -> atomically claim SourceMaterial(parsing)
  -> run bounded parser subprocess
  -> validate ParsedMaterial contract
  -> commit parsed_material Artifact (derived_from source)
  -> SourceMaterial(ready)
```

An upload request ends after the original Artifact and material record are durable. Parsing is performed by the existing in-process supervision boundary, but risky file decoding happens in a child process. No independent worker, Redis, lease, or heartbeat is introduced.

The original Artifact is the recovery anchor. A parser crash or timeout never requires the user to upload the same bytes again.

## 3. Persistent Model and State Machine

Add a `source_materials` table with these logical fields:

- `id`, `project_id`;
- `display_name`, `media_type`;
- `source_artifact_id`, nullable `parsed_artifact_id`;
- `status`: `uploaded`, `parsing`, `ready`, `failed`, `deleted`;
- `parser_name`, `parser_version`, and normalized `limits_json`;
- nullable normalized `error_code` and safe `error_detail`;
- `attempt_count`, timestamps, and optimistic `version`.

Database constraints require both Artifact references to exist. Service-level checks require both Artifacts and the material to belong to the same project; the migration should use composite foreign keys where the existing Artifact schema permits them. `parsed_artifact_id` may only be set for `ready` records.

Allowed transitions are:

```text
uploaded -> parsing
parsing  -> ready
parsing  -> failed
failed   -> parsing       # explicit retry only
uploaded -> deleted
failed   -> deleted
ready    -> deleted       # removes current references, not immutable history
```

At startup, a `parsing` record without a committed parsed Artifact returns to `failed` with `parser_interrupted`. If a deterministic parsed Artifact for the material attempt was committed before the crash, reconciliation attaches it and marks the material `ready`. Parsing is local and non-billable, so an interrupted attempt is safe to retry explicitly; it is not treated as a remote `outcome_unknown` operation.

Each successful parse produces a new immutable parsed Artifact. Retry never overwrites a previous Artifact.

## 4. Upload Boundary

Expose an authenticated endpoint under the existing versioned API:

```text
POST /api/v1/projects/{project_id}/materials?filename=<display-name>
Content-Type: application/octet-stream | text/markdown | application/pdf | image/*
```

The body is streamed into Artifact staging. The endpoint:

1. authorizes the project before reading the body;
2. applies the configured per-file ceiling while streaming;
3. checks the project's current non-deleted source-byte total before final commit and rechecks it transactionally when the material is created;
4. rejects empty uploads;
5. identifies content from signatures and validated decoding, using the filename only as display metadata;
6. permits UTF-8 Markdown, PDF, PNG, JPEG, and WebP only; and
7. commits the original as a `source_material` Artifact before returning `202 Accepted`.

The server never accepts a filesystem path from the client. Display names are normalized, stripped of directory components and control characters, length-limited, and never used as storage paths.

Upload failures remove or reconcile staging state through the existing Artifact protocol. Error responses contain stable codes and no source content or local paths.

## 5. Parser Isolation and Limits

The parser coordinator launches one child process per attempt using the multiprocessing `spawn` context. The child receives a validated workspace path resolved from the Artifact ID, a read-only parse request, an isolated temporary directory, and a serialized limits snapshot.

The parent enforces `parser_timeout_seconds`, terminates and joins a timed-out child, validates the returned message size and schema, and maps abnormal exits to stable failure codes. On POSIX, the child additionally applies available CPU and address-space limits. On Windows, the guaranteed boundary is process separation, an isolated temporary directory, a wall-clock timeout, and forced termination; the product does not claim a strong Windows memory sandbox in this milestone.

The child cannot mutate Workflow state, call providers, fetch external resources, or choose arbitrary output paths. It returns a bounded structured result over an IPC channel; the parent alone commits Artifacts and state transitions.

### 5.1 Markdown

Markdown must be valid UTF-8. The parser enforces:

- maximum character count;
- structural nesting depth for block quotes and lists;
- external image/link resource count; and
- output text and block count ceilings derived from the input limit.

Links and images are recorded as references but are never fetched. HTML blocks are preserved as inert text or omitted with a warning; scripts and embedded executable content are never executed.

### 5.2 PDF

The PDF parser performs cheap header and size checks before full parsing, then enforces:

- maximum page count;
- maximum indirect-object count;
- encryption rejection unless the file is readable without a password;
- parser timeout; and
- bounded extracted text and metadata output.

Text is extracted page by page with stable page locators. A page with no extractable text is recorded with `text_unavailable`; OCR is not silently attempted. Malformed object graphs, parser crashes, and limit violations produce stable failure codes.

### 5.3 Images

The image parser verifies the decoded format and enforces:

- allowed format;
- width, height, and total-pixel limit;
- frame-count limit;
- decoded-memory estimate; and
- compressed-to-decoded ratio.

The image is fully decoded and verified before acceptance. Metadata returned to the parent is limited to dimensions, format, mode, frame count, and selected orientation/profile facts. Pixel data is not duplicated into the parsed JSON Artifact.

## 6. Parsed Material Contract

Add a strict, versioned `ParsedMaterialV1` contract containing:

- `schema_version = 1`;
- `material_id`, `media_type`, and `source_artifact_id`;
- `untrusted_source_content = true` as a required literal;
- `text_blocks`, each with stable ID, text, kind, and a source locator;
- document metadata such as PDF page count or image dimensions;
- external references recorded without retrieval;
- warnings, including scanned pages or omitted inert content; and
- parser identity and limits fingerprint.

Source locators use media-specific fields: Markdown line spans, PDF page numbers, or image metadata scope. Text block IDs are deterministic from material identity, source locator, and normalized content so retries of the same parser version are comparable.

The serialized contract is committed as a `parsed_material` JSON Artifact with a `derived_from` edge to the original. Artifact cache identity includes original content hash, parser name/version, contract version, and limits fingerprint.

## 7. Service and API Surface

Create a focused material service responsible for upload finalization, state transitions, parse scheduling, reconciliation, query projection, retry, and logical deletion.

Expose authenticated, project-authorized routes:

- `POST /projects/{project_id}/materials` — upload and queue parsing;
- `GET /projects/{project_id}/materials` — list material projections;
- `GET /projects/{project_id}/materials/{material_id}` — detail and warnings;
- `POST /projects/{project_id}/materials/{material_id}/retry` — retry a failed local parse;
- `DELETE /projects/{project_id}/materials/{material_id}` — remove current project references without deleting immutable history.

All mutation routes keep the existing Host, Origin, Bearer, and project authorization requirements. Cross-project material IDs return 404 rather than revealing ownership.

## 8. Planning Integration

Extend planning-run creation with `material_ids`. Before creating a run, the server verifies that every material:

- belongs to the authorized project;
- is `ready`;
- points to an available parsed Artifact; and
- still satisfies the expected parsed contract version.

The server builds planning input from validated parsed Artifacts in request order. Each block is serialized with an explicit material and locator boundary and remains marked as untrusted source content. Client-supplied `source_refs` are removed from the public request model; internal workflow calls may continue to carry validated Artifact IDs.

Direct `normalized_text` remains available for typed user input and retains its existing size limit. When both typed text and materials exist, the context compiler joins them deterministically and records every parsed Artifact as a source reference.

## 9. Concurrency, Recovery, and Deletion

Material claiming uses an atomic conditional update so duplicate scheduling cannot run two parser attempts for the same version. Project and global parse concurrency use the existing supervisor concepts but have their own limits so parsing cannot starve planning or provider calls.

The raw Artifact remains available after parse failure. Explicit retry increments the attempt counter and preserves the prior safe error. Logical deletion removes active references and prevents new planning runs from using the material; physical collection remains governed by Artifact retention and GC.

Application startup performs material reconciliation after Artifact reconciliation and before readiness. A ready material with a missing or invalid parsed Artifact becomes `failed` with `parsed_artifact_unavailable` and is not supplied to planning.

## 10. Errors and Observability

Stable error codes include:

- `unsupported_media_type`, `empty_upload`, `file_too_large`, `project_upload_limit`;
- `invalid_utf8`, `markdown_too_deep`, `external_resource_limit`;
- `pdf_encrypted`, `pdf_page_limit`, `pdf_object_limit`, `pdf_malformed`;
- `image_pixel_limit`, `image_frame_limit`, `image_compression_limit`, `image_decode_failed`;
- `parser_timeout`, `parser_crashed`, `parser_output_invalid`; and
- `parsed_artifact_unavailable`.

Events record material ID, status, media class, byte counts, elapsed time, parser version, and safe error code. They do not copy extracted text, API keys, absolute paths, or raw filenames. Parser diagnostics are bounded and sanitized before persistence.

## 11. Dependencies

Use focused libraries with pinned compatible ranges:

- Pillow for PNG/JPEG/WebP metadata and full decode verification;
- pypdf for PDF structure inspection and text extraction.

Markdown structural validation remains local and deterministic; no browser or network-capable Markdown renderer is needed in this milestone.

## 12. Verification Strategy

Tests cover:

- state-machine transitions, optimistic concurrency, duplicate claims, and migration constraints;
- streamed byte ceilings, project cumulative limits, empty input, filename normalization, spoofed extensions, and unsupported formats;
- Markdown UTF-8, depth, external-resource, inert HTML, and deterministic locator behavior;
- PDF pages, object counts, encryption, malformed files, scanned-page warnings, timeout, and child-process crashes;
- image dimensions, total pixels, multi-frame rejection, compression ratio, truncated data, and full-decode verification;
- Artifact dependency edges, crash points between file commit and state commit, startup reconciliation, explicit retry, and logical deletion;
- authentication, Host/Origin validation, project ownership, and cross-project material references;
- planning creation from ready materials and rejection of pending, failed, deleted, stale, invalid, or foreign material IDs; and
- full regression suite, `compileall`, and `git diff --check`.

Tests use generated minimal fixtures and do not depend on network access or real provider credentials.

## 13. Non-goals and Follow-on Interfaces

This milestone does not implement OCR, Qwen, Seedream, page rendering, export, or UI. It deliberately leaves extension points for:

- OCR enriching a new parsed-material version rather than mutating V1;
- Qwen consuming the original image Artifact through a provider operation;
- a context compiler selecting text blocks by stable source locator; and
- renderer and export workflows depending on immutable, project-owned source Artifacts.

The next architectural subproject can therefore add visual providers without changing upload ownership, parser isolation, or planning source validation.
