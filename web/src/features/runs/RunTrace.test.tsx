import { render, screen } from "@testing-library/react";
import { fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "../../api/client";
import { RunTrace } from "./RunTrace";

const runId = "run-failed";

beforeEach(() => {
  vi.spyOn(apiClient, "request").mockResolvedValue({
    run: { id: runId, project_id: "project-1", workflow_name: "deck_planning", workflow_version: 1, status: "failed", error: { code: "content_rejected" }, raw_payload: "do not display" },
    steps: [{ id: "step-1", status: "failed", step_key: "brief", generation: 1, attempt_count: 1, error: { code: "content_rejected" }, prompt: "do not display" }],
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

  expect((await screen.findAllByText(/模型返回内容未通过结构校验/)).length).toBeGreaterThan(0);
  expect(screen.getByText("run.failed")).toBeInTheDocument();
  expect(screen.getByText("brief")).toBeInTheDocument();
  expect(screen.getByText("2026-10-04T10:01:00Z")).toBeInTheDocument();
  expect(screen.queryByText("do not display")).not.toBeInTheDocument();
  expect(screen.queryByText("Bearer do-not-display")).not.toBeInTheDocument();
  expect(document.querySelectorAll("#run-trace")).toHaveLength(1);
});

it("keeps the trace anchor while loading", () => {
  vi.mocked(apiClient.request).mockImplementation(() => new Promise(() => {}));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunTrace runId={runId} /></QueryClientProvider>);
  expect(document.querySelectorAll("#run-trace")).toHaveLength(1);
});

it("shows an accessible Chinese trace alert and anchor when loading fails", async () => {
  vi.mocked(apiClient.request).mockRejectedValue(new Error("unavailable"));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunTrace runId={runId} /></QueryClientProvider>);
  expect(await screen.findByRole("alert")).toHaveTextContent("无法加载运行轨迹");
  expect(document.querySelectorAll("#run-trace")).toHaveLength(1);
});

it("authorizes only an outcome_unknown attempt with its exact identity", async () => {
  vi.mocked(apiClient.request).mockImplementation(async (path, options) => {
    if (path.endsWith("/trace")) return {
      run: { id: runId, project_id: "project-1", workflow_name: "deck_production", workflow_version: 1, status: "recovery_required", error: null },
      steps: [], events: [], operations: [{
        operation: { id: "op-1", operation_kind: "image.generate", provider: "ark", model: "seedream", aggregate_status: "outcome_unknown", selected_attempt_id: null, has_unknown_attempts: true, step_id: null, page_id: null },
        attempts: [
          { id: "attempt-unknown", attempt_no: 1, status: "outcome_unknown", retry_authorized: false, request_started_at: null, response_received_at: null, error_code: "unknown_remote_outcome", response_summary: null },
          { id: "attempt-reconciling", attempt_no: 2, status: "reconciling", retry_authorized: false, request_started_at: null, response_received_at: null, error_code: null, response_summary: null },
        ],
      }],
    };
    if (path === "/api/v1/operations/op-1/authorize-retry") return { id: "attempt-new" };
    throw new Error(`unexpected request ${path} ${JSON.stringify(options)}`);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunTrace runId={runId} projectId="project-1" /></QueryClientProvider>);

  fireEvent.click(await screen.findByRole("button", { name: "处理未知结果" }));
  expect(screen.getAllByText(/reconciling/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("checkbox", { name: /Provider 控制台/ }));
  fireEvent.click(screen.getByRole("button", { name: "授权重试" }));

  await waitFor(() => expect(apiClient.request).toHaveBeenCalledWith(
    "/api/v1/operations/op-1/authorize-retry",
    { method: "POST", body: { expected_attempt_id: "attempt-unknown", reason: "用户已在 Provider 控制台核对结果并接受可能重复计费的风险" } },
  ));
  expect(screen.getAllByRole("button", { name: "处理未知结果" })).toHaveLength(1);
});
