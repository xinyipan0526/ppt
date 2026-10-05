import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../api/client";
import { ProjectWorkspace } from "./ProjectWorkspace";

class TestEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener() {}
  close() {}
}

const runId = "run-failed";
const workspace = {
  project: { id: "project-1", name: "Audit deck" },
  runs: [{ id: runId, workflow: "deck_planning", status: "failed", created_at: "2026-10-04T10:00:00Z" }],
  materials: [],
  deck_generations: [],
};

const trace = {
  run: { id: runId, project_id: "project-1", workflow_name: "deck_planning", workflow_version: 1, status: "failed", error: { code: "invalid_request" } },
  steps: [{ id: "step-1", status: "failed", step_key: "brief", generation: 1, attempt_count: 1, error: { code: "invalid_request" } }],
  events: [{ event_id: "event-1", run_seq: 1, event_type: "run.failed", schema_version: 1, occurred_at: "2026-10-04T10:01:00Z", recorded_at: "2026-10-04T10:01:00Z", step_id: null, page_id: null, operation_id: null, trace_id: "trace-1", durability: "core" }],
  operations: [],
};

function renderWorkspace() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/projects/project-1"]}>
        <Routes><Route path="/projects/:projectId" element={<ProjectWorkspace />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectWorkspace terminal runs", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", TestEventSource);
    vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return workspace;
      if (path.endsWith("/snapshot")) return { run: { id: runId, status: "failed" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return trace;
      throw new Error(`unexpected request ${path}`);
    });
  });

  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("retains a failed run with its safe error until the user dismisses it", async () => {
    renderWorkspace();

    expect((await screen.findAllByText("invalid_request")).length).toBeGreaterThan(0);
    expect(screen.getByText(runId)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss run" }));

    await waitFor(() => expect(screen.queryByText(runId)).not.toBeInTheDocument());
  });
});
