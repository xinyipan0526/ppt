import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "../../api/client";
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

function renderWorkspace(queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={queryClient}>
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
      if (path === "/api/v1/provider-settings") return { mode: "deterministic", configured: false, setup_required: false };
      if (path.endsWith("/snapshot")) return { run: { id: runId, status: "failed", error: { code: "invalid_request" } }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return trace;
      throw new Error(`unexpected request ${path}`);
    });
  });

  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("shows the project ledger currencies in the workspace header", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return workspace;
      if (path.endsWith("/costs")) return { project_id: "project-1", totals: [], currencies: [{ currency: "CNY", hard_limit: 5000, settled: 1020, active_reserved: 480, held_unknown: 130, available: 3370 }] };
      if (path === "/api/v1/provider-settings") return { mode: "deterministic", configured: false, setup_required: false };
      if (path.endsWith("/snapshot")) return { run: { id: runId, status: "failed" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return trace;
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();
    expect(await screen.findByText("¥10.20")).toBeInTheDocument();
    expect(screen.getByText("¥33.70")).toBeInTheDocument();
  });

  it("retains a failed run with its safe error until the user dismisses it", async () => {
    renderWorkspace();

    expect((await screen.findAllByText(/模型请求参数不兼容/)).length).toBeGreaterThan(0);
    expect(vi.mocked(apiClient.request).mock.calls.filter(([path]) => String(path).endsWith("/trace"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
    expect(await screen.findByText(runId)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭任务" }));

    await waitFor(() => expect(screen.queryByText(runId)).not.toBeInTheDocument());
  });

  it("shows the safe API error when production launch is rejected", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return { ...workspace, runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }] };
      if (path === "/api/v1/provider-settings") return { mode: "deterministic", configured: false, setup_required: false };
      if (path.endsWith("/snapshot")) return { run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return trace;
      if (path.endsWith("/production-runs")) throw new ApiError("规划版本已过期，请先刷新。", 409);
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "开始生成 PPT" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("规划版本已过期，请先刷新。");
  });

  it("blocks only new paid actions while retaining existing cloud workspace data", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return {
        ...workspace,
        runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }],
        materials: [{ id: "material-old", filename: "existing.pdf", media_type: "application/pdf", status: "ready", created_at: "2026-10-04T10:00:00Z" }],
        deck_generations: [{ id: "deck-old", project_id: "project-1", planning_run_id: "plan-approved", production_run_id: "production-old", generation: 1, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-04T10:00:00Z" }],
      };
      if (path === "/api/v1/provider-settings") return { mode: "cloud", configured: false, setup_required: true };
      if (path.endsWith("/snapshot")) return { run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return { ...trace, run: { ...trace.run, id: "plan-approved", status: "succeeded" } };
      if (path.endsWith("/production")) return { run_id: "production-old", status: "succeeded", pages: [] };
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    expect(await screen.findByRole("alert")).toHaveTextContent("请先完成模型设置");
    expect(screen.getByRole("link", { name: "前往模型设置" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("button", { name: "开始规划" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "全局重新生成" })).toBeDisabled();
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "任务进度" })).toBeInTheDocument();
    expect(screen.getAllByText("succeeded")).toHaveLength(2);
    expect(await screen.findByRole("heading", { name: "完整成品" })).toBeInTheDocument();
  });

  it("blocks production launch while retaining an approved cloud planning run", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return {
        ...workspace,
        runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }],
        materials: [{ id: "material-old", filename: "existing.pdf", media_type: "application/pdf", status: "ready", created_at: "2026-10-04T10:00:00Z" }],
      };
      if (path === "/api/v1/provider-settings") return { mode: "cloud", configured: false, setup_required: true };
      if (path.endsWith("/snapshot")) return { run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return { ...trace, run: { ...trace.run, id: "plan-approved", status: "succeeded" } };
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    expect(await screen.findByRole("button", { name: "开始生成 PPT" })).toBeDisabled();
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
    const runStatus = await screen.findByRole("heading", { name: "任务进度" });
    expect(runStatus.closest("section")).toHaveTextContent("succeeded");
  });

  it("keeps an open paid dialog and existing data visible when a settings refetch reports setup required", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let providerReads = 0;
    let resolveProviderRefetch: ((value: { mode: "cloud"; configured: boolean; setup_required: boolean }) => void) | undefined;
    vi.mocked(apiClient.request).mockImplementation((path) => {
      if (path.endsWith("/workspace")) return Promise.resolve({
        ...workspace,
        runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }],
        materials: [{ id: "material-old", filename: "existing.pdf", media_type: "application/pdf", status: "ready", created_at: "2026-10-04T10:00:00Z" }],
        deck_generations: [{ id: "deck-old", project_id: "project-1", planning_run_id: "plan-approved", production_run_id: "production-old", generation: 1, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-04T10:00:00Z" }],
      });
      if (path === "/api/v1/provider-settings") {
        providerReads += 1;
        return providerReads === 1
          ? Promise.resolve({ mode: "cloud", configured: true, setup_required: false })
          : new Promise((resolve) => { resolveProviderRefetch = resolve; });
      }
      if (path.endsWith("/snapshot")) return Promise.resolve({ run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 });
      if (path.endsWith("/trace")) return Promise.resolve({ ...trace, run: { ...trace.run, id: "plan-approved", status: "succeeded" } });
      if (path.endsWith("/production")) return Promise.resolve({ run_id: "production-old", status: "succeeded", pages: [] });
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace(queryClient);

    fireEvent.click(await screen.findByRole("button", { name: "全局重新生成" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    void queryClient.invalidateQueries({ queryKey: ["provider-settings"] });

    expect(await screen.findByRole("button", { name: "创建新版本" })).toBeDisabled();
    resolveProviderRefetch?.({ mode: "cloud", configured: false, setup_required: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("请先完成模型设置");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建新版本" })).toBeDisabled();
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "完整成品" })).toBeInTheDocument();
  });

  it("does not block deterministic mode when Ark is not configured", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return { ...workspace, runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }] };
      if (path === "/api/v1/provider-settings") return { mode: "deterministic", configured: false, setup_required: false };
      if (path.endsWith("/snapshot")) return { run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 };
      if (path.endsWith("/trace")) return trace;
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    expect(await screen.findByRole("button", { name: "开始生成 PPT" })).toBeInTheDocument();
    expect(screen.queryByText("请先完成模型设置")).not.toBeInTheDocument();
  });

  it("keeps all workspace actions unavailable while provider settings are loading", async () => {
    vi.mocked(apiClient.request).mockImplementation((path) => {
      if (path.endsWith("/workspace")) return Promise.resolve({ ...workspace, runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }] });
      if (path === "/api/v1/provider-settings") return new Promise(() => undefined);
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    expect(await screen.findByRole("status")).toHaveTextContent("正在检查模型设置");
    expect(screen.queryByRole("button", { name: "开始规划" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开始生成 PPT" })).not.toBeInTheDocument();
  });

  it("keeps all workspace actions unavailable while cached provider settings refresh", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["provider-settings"], {
      mode: "deterministic",
      configured: false,
      setup_required: false,
    });
    vi.mocked(apiClient.request).mockImplementation((path) => {
      if (path.endsWith("/workspace")) return Promise.resolve({ ...workspace, runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }] });
      if (path === "/api/v1/provider-settings") return new Promise(() => undefined);
      if (path.endsWith("/snapshot")) return Promise.resolve({ run: { id: "plan-approved", status: "succeeded" }, steps: [], pages: [], interrupts: [], snapshot_seq: 2 });
      if (path.endsWith("/trace")) return Promise.resolve(trace);
      throw new Error(`unexpected request ${path}`);
    });

    renderWorkspace(queryClient);

    expect(await screen.findByRole("button", { name: "开始规划" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始生成 PPT" })).toBeDisabled();
    expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
  });

  it("keeps all workspace actions unavailable when provider settings cannot load", async () => {
    vi.mocked(apiClient.request).mockImplementation(async (path) => {
      if (path.endsWith("/workspace")) return { ...workspace, runs: [{ id: "plan-approved", workflow: "deck_planning", status: "succeeded", created_at: "2026-10-04T10:00:00Z" }] };
      if (path === "/api/v1/provider-settings") throw new Error("offline");
      throw new Error(`unexpected request ${path}`);
    });
    renderWorkspace();

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取模型设置");
    expect(screen.queryByRole("button", { name: "开始规划" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开始生成 PPT" })).not.toBeInTheDocument();
  });
});
