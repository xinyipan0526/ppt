import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "../../api/client";
import { ApiError } from "../../api/client";
import { RunStatus } from "./RunStatus";

const runId = "run-failed";

beforeEach(() => {
  vi.stubGlobal("EventSource", class {
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    onerror: (() => void) | null = null;
    addEventListener() {}
    close() {}
  });
  vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) {
      return {
        run: {
          id: runId,
          status: "failed",
          error: {
            code: "invalid_request",
            provider_summary: { http_status: 400, provider_error_type: "invalid_request" },
          },
        },
        steps: [], pages: [], interrupts: [], snapshot_seq: 2,
      };
    }
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps trace lazy and preserves the snapshot error when details fail", async () => {
  const onDismiss = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunStatus runId={runId} onChange={() => {}} onDismiss={onDismiss} />
    </QueryClientProvider>,
  );

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("模型请求参数不兼容");
  expect(alert).toHaveTextContent("HTTP 400");
  expect(alert).toHaveTextContent("打开运行详情");
  expect(document.getElementById("run-trace")).not.toBeInTheDocument();
  expect(vi.mocked(apiClient.request).mock.calls.filter(([path]) => String(path).endsWith("/trace"))).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "打开运行详情" }));
  expect(await screen.findByText("无法加载运行轨迹，请稍后重试。")).toBeInTheDocument();
  expect(alert).toHaveTextContent("模型请求参数不兼容");
  expect(vi.mocked(apiClient.request).mock.calls.filter(([path]) => String(path).endsWith("/trace"))).toHaveLength(1);
  expect(screen.getByRole("button", { name: "关闭提示" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "关闭提示" })).not.toBeInTheDocument());
  expect(onDismiss).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "任务进度" })).toBeInTheDocument();
});

it("shows a Chinese retryable alert instead of a permanent skeleton when snapshot loading fails", async () => {
  let snapshotAttempts = 0;
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) {
      snapshotAttempts += 1;
      if (snapshotAttempts === 1) throw new ApiError("服务暂不可用", 503);
      return { run: { id: runId, status: "queued" }, steps: [], pages: [], interrupts: [], snapshot_seq: 3 };
    }
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunStatus runId={runId} onChange={() => {}} onDismiss={() => {}} />
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取任务状态：服务暂不可用");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByRole("heading", { name: "任务进度" })).toBeInTheDocument();
});

it("shows a new terminal notice after the same run recovers and fails again", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunStatus runId={runId} onChange={() => {}} onDismiss={() => {}} />
    </QueryClientProvider>,
  );

  await screen.findByRole("button", { name: "关闭提示" });
  fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "关闭提示" })).not.toBeInTheDocument());

  client.setQueryData(["run-snapshot", runId], { run: { id: runId, status: "queued" }, steps: [], pages: [], interrupts: [], snapshot_seq: 3 });
  await screen.findByText("queued");
  client.setQueryData(["run-snapshot", runId], { run: { id: runId, status: "failed", error: { code: "invalid_request" } }, steps: [], pages: [], interrupts: [], snapshot_seq: 4 });

  expect(await screen.findByRole("button", { name: "关闭提示" })).toBeInTheDocument();
});

it("blocks paid recovery while preserving run inspection and cancellation", async () => {
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) return {
      run: { id: runId, status: "recovery_required", error: { code: "recovery_required" } },
      steps: [], pages: [], interrupts: [], snapshot_seq: 3,
    };
    if (path.endsWith("/trace")) return {
      run: { id: runId, project_id: "project-1", workflow_name: "deck_planning", workflow_version: 2, status: "recovery_required", error: null },
      steps: [], events: [], operations: [],
    };
    throw new Error(`unexpected request ${path}`);
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunStatus runId={runId} paidActionsDisabled onChange={() => {}} onDismiss={() => {}} />
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "任务进度" })).toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
  await screen.findByLabelText("Run trace");
  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(screen.getByRole("button", { name: "授权恢复" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "取消任务" })).toBeEnabled();
});

it("confirms cancellation once and projects page progress from the snapshot", async () => {
  let cancellationRequests = 0;
  let resolveCancellation: (() => void) | undefined;
  let status = "running";
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) {
      return {
        run: { id: runId, status },
        steps: [
          { id: "completed-step", status: "succeeded", values: { step_key: "已经完成的步骤" } },
          { id: "current-step", status: "running", values: { step_key: "正在生成页面" } },
        ],
        pages: [
          { id: "page-1", status: "succeeded", values: {} },
          { id: "page-2", status: "failed", values: {} },
          { id: "page-3", status: "running", values: {} },
        ],
        interrupts: [], snapshot_seq: 5,
      };
    }
    if (path.endsWith("/cancel")) {
      cancellationRequests += 1;
      await new Promise<void>((resolve) => { resolveCancellation = resolve; });
      status = "cancel_requested";
      return { run_id: runId, status, accepted: true };
    }
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunStatus runId={runId} onChange={() => {}} onDismiss={() => {}} />
    </QueryClientProvider>,
  );

  expect(await screen.findByLabelText("页面进度")).toHaveTextContent("1 / 3 已完成 · 1 失败");
  expect(screen.getByText("正在生成页面")).toBeInTheDocument();
  expect(screen.queryByText("已经完成的步骤")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
  const confirm = screen.getByRole("button", { name: "确认取消任务" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await waitFor(() => expect(screen.getByRole("button", { name: "正在取消…" })).toBeDisabled());

  resolveCancellation?.();

  expect(await screen.findByText("cancel_requested")).toBeInTheDocument();
  expect(cancellationRequests).toBe(1);
});

it("shows the safe API error when cancellation is rejected", async () => {
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) return { run: { id: runId, status: "running" }, steps: [], pages: [], interrupts: [], snapshot_seq: 5 };
    if (path.endsWith("/cancel")) throw new ApiError("任务已进入不可取消状态。", 409);
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunStatus runId={runId} onChange={() => undefined} onDismiss={() => undefined} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "取消任务" }));
  fireEvent.click(screen.getByRole("button", { name: "确认取消任务" }));
  await waitFor(() => expect(screen.getAllByRole("alert").some((alert) => alert.textContent?.includes("任务已进入不可取消状态。"))).toBe(true));
});

it("lets a cancel_requested run explicitly retry only cancellation finalization", async () => {
  let attempts = 0;
  let status = "cancel_requested";
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) return { run: { id: runId, status }, steps: [], pages: [], interrupts: [], snapshot_seq: attempts + 1 };
    if (path.endsWith("/cancel")) {
      attempts += 1;
      if (attempts === 1) throw new ApiError("cancellation finalizer unavailable", 503);
      status = "cancelled";
      return { run_id: runId, status, accepted: false };
    }
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunStatus runId={runId} onChange={() => undefined} onDismiss={() => undefined} /></QueryClientProvider>);

  const retry = await screen.findByRole("button", { name: "继续完成取消" });
  fireEvent.click(retry);
  const cancellationError = await screen.findByText("cancellation finalizer unavailable");
  expect(cancellationError.closest('[role="alert"]')).toHaveTextContent("cancellation finalizer unavailable");
  fireEvent.click(screen.getByRole("button", { name: "继续完成取消" }));
  expect(await screen.findByText("cancelled")).toBeInTheDocument();
  expect(attempts).toBe(2);
});

it("shows Chinese retry guidance, persisted due time, and real page progress", async () => {
  vi.mocked(apiClient.request).mockImplementation(async (path) => {
    if (path.endsWith("/snapshot")) return {
      run: { id: runId, status: "waiting_for_retry", retry_at: "2026-10-14T13:30:00Z" },
      steps: [{ id: "step-1", status: "waiting_for_retry", values: { step_key: "生成视觉" } }],
      pages: [{ id: "p1", status: "succeeded", values: {} }, { id: "p2", status: "running", values: {} }], interrupts: [], snapshot_seq: 6,
    };
    if (path.endsWith("/trace")) throw new Error("trace unavailable");
    throw new Error(`unexpected request ${path}`);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RunStatus runId={runId} onChange={() => undefined} onDismiss={() => undefined} /></QueryClientProvider>);
  expect((await screen.findAllByText("等待自动重试")).length).toBeGreaterThan(0);
  expect(screen.getByText(/2026\/10\/14/)).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "页面进度：1 / 2 已完成，0 失败" })).toHaveAttribute("aria-valuenow", "1");
});
