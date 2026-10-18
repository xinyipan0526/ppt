# Audit Logging and Run Trace Design

## Goal

Make planning failures visible to users and auditable by preserving structured,
redacted application logs on disk and exposing a project-scoped Run trace.

## Scope

- Write structured JSONL logs below the configured Workspace at
  `logs/ppt-agent.jsonl`, with bounded rotation.
- Attach project, Run, Step, Operation, Provider, and trace identifiers when
  available.
- Redact credentials, authorization headers, raw prompts, source content, and
  local secret paths before persistence.
- Preserve safe Provider failure metadata (`http_status`, normalized error code,
  exception class) in Attempt, Step, Run, and logs.
- Add a read-only Run trace projection containing the Run snapshot, ordered
  workflow events, and safe Provider operation/attempt summaries.
- Keep terminal failed Runs visible in the React workspace and provide a trace
  view with a human-readable failure message and Run ID.

## Non-goals

- No raw model request/response bodies or API keys in logs, events, or trace API.
- No external log collector, OpenTelemetry deployment, or multi-process queue.
- No automatic retry for an unknown remote outcome.
- No change to the existing state machine semantics.

## Architecture

The composition root configures one process-wide redacting JSON logger after the
Workspace is known. Runtime boundaries emit records through a small adapter that
accepts structured context and never serializes arbitrary request bodies. A
rotating file handler writes JSONL to the Workspace logs directory.

The trace endpoint reads the existing SQLite event projection and Provider
operation tables in one bounded read. It returns safe summaries only and is
authorized with the existing project/Run ownership checks. The frontend retains
the selected terminal Run until the user dismisses it, renders the safe error,
and polls/SSE-refreshes its trace while active.

## Data contracts

Each log record contains `timestamp`, `level`, `logger`, `message`, and optional
`project_id`, `run_id`, `step_id`, `operation_id`, `provider`, `model`,
`event_type`, `status`, `error_code`, `http_status`, `duration_ms`, and
`trace_id`. Values are bounded strings or numbers. Redaction replaces sensitive
values with `[REDACTED]`.

The trace response contains:

```json
{
  "run": {"id": "...", "status": "failed", "error": {"code": "invalid_request"}},
  "steps": [],
  "events": [],
  "operations": [{"operation": {}, "attempts": []}]
}
```

The endpoint must cap event and operation result counts and must not expose raw
Provider payloads or input text.

## Failure behavior

Known Provider failures remain known failures and are not retried automatically.
The safe Provider response summary is persisted on the Attempt and copied into
the Step/Run failure projection. If a response cannot be proven uncharged, the
existing `outcome_unknown` recovery path remains unchanged.

## Verification

- Unit tests cover redaction, JSONL formatting, rotation configuration, and
  bounded context.
- Integration tests cover safe Provider error persistence and the authorized
  trace projection.
- React tests cover failed terminal Run visibility and trace rendering.
- Existing provider, planning, runtime, and frontend suites remain green.
