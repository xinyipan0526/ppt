import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "../../api/client";
import { RunTrace } from "./RunTrace";

const runId = "run-failed";

beforeEach(() => {
  vi.spyOn(apiClient, "request").mockResolvedValue({
    run: { id: runId, project_id: "project-1", workflow_name: "deck_planning", workflow_version: 1, status: "failed", error: { code: "invalid_request", provider_summary: { http_status: 400 } }, raw_payload: "do not display" },
    steps: [{ id: "step-1", status: "failed", step_key: "brief", generation: 1, attempt_count: 1, error: { code: "invalid_request" }, prompt: "do not display" }],
    events: [{ event_id: "event-1", run_seq: 1, event_type: "run.failed", schema_version: 1, occurred_at: "2026-10-04T10:01:00Z", recorded_at: "2026-10-04T10:01:01Z", step_id: "step-1", page_id: null, operation_id: null, trace_id: "trace-1", durability: "core", payload: { authorization: "Bearer do-not-display" } }],
    operations: [],
  });
});

afterEach(() => vi.restoreAllMocks());

it("renders only the safe ordered trace fields", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunTrace runId={runId} />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("invalid_request")).toBeInTheDocument();
  expect(screen.getByText("run.failed")).toBeInTheDocument();
  expect(screen.getByText("brief")).toBeInTheDocument();
  expect(screen.getByText("2026-10-04T10:01:00Z")).toBeInTheDocument();
  expect(screen.queryByText("do not display")).not.toBeInTheDocument();
  expect(screen.queryByText("Bearer do-not-display")).not.toBeInTheDocument();
});
