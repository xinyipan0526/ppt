import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { useLayoutEffect } from "react";
import { ApiError, apiClient } from "../../api/client";
import type { ExportSnapshot } from "../../api/contracts";
import { ExportPanel } from "./ExportPanel";

function snapshot(
  status: ExportSnapshot["status"],
  error: ExportSnapshot["error"] = null,
  overrides: Partial<ExportSnapshot> = {},
): ExportSnapshot {
  return {
    id: "export-1", project_id: "project-1", run_id: "run-1", format: "pptx",
    status, artifact_id: status === "available" ? "artifact-1" : null, error,
    attempt_count: status === "queued" ? 0 : 1,
    started_at: status === "queued" ? null : "2026-10-08T00:00:00Z",
    finished_at: ["available", "failed", "cancelled"].includes(status) ? "2026-10-08T00:00:01Z" : null,
    ...overrides,
  };
}

function renderPanel(runId = "run-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><ExportPanel runId={runId} /></QueryClientProvider>);
  return {
    ...view,
    rerenderRun(nextRunId: string) {
      view.rerender(<QueryClientProvider client={client}><ExportPanel runId={nextRunId} /></QueryClientProvider>);
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => sessionStorage.clear());
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("polls the same queued export until available and only then offers download", async () => {
  vi.useFakeTimers();
  let getCount = 0;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued") as never;
    getCount += 1;
    return snapshot(getCount === 1 ? "running" : "available") as never;
  });
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(screen.queryByRole("link", { name: "下载 PPTX" })).not.toBeInTheDocument();
  expect(screen.getByText(/正在导出|排队中/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4_000);
    await Promise.resolve();
  });
  expect(screen.getByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-1/download");
  expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1").length).toBeGreaterThanOrEqual(2);
});

it("recovers the persisted export ID after remount without posting again", async () => {
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued") as never;
    return snapshot("running") as never;
  });
  const first = renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1")).toHaveLength(1));
  first.unmount();
  renderPanel();
  await waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1").length).toBeGreaterThanOrEqual(2));
  expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect(await screen.findByText("正在导出…")).toBeInTheDocument();
});

it("shows safe failed detail and never renders a premature download", async () => {
  vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued") as never;
    return snapshot("failed", { code: "encoder_failed", detail: "编码器无法完成导出" }) as never;
  });
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("编码器无法完成导出");
  expect(screen.queryByRole("link", { name: "下载 PPTX" })).not.toBeInTheDocument();
});

it("sends only relevant bounded quality options", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue(snapshot("queued") as never);
  renderPanel();
  fireEvent.change(screen.getByRole("spinbutton", { name: "JPG 质量" }), { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: "生成 JPG ZIP" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith(
    "/api/v1/runs/run-1/exports",
    { method: "POST", body: { format: "jpg_zip", forced: false, jpeg_quality: 80 } }
  ));
  expect(screen.getByRole("spinbutton", { name: "JPG 质量" })).toHaveAttribute("min", "1");
  expect(screen.getByRole("spinbutton", { name: "JPG 质量" })).toHaveAttribute("max", "100");
  expect(screen.getByRole("spinbutton", { name: "PDF DPI" })).toHaveAttribute("min", "72");
  expect(screen.getByRole("spinbutton", { name: "PDF DPI" })).toHaveAttribute("max", "600");
});

it("sends PDF DPI without an unrelated JPEG option", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue(snapshot("queued") as never);
  renderPanel();
  fireEvent.change(screen.getByRole("spinbutton", { name: "PDF DPI" }), { target: { value: "300" } });
  fireEvent.click(screen.getByRole("button", { name: "生成 PDF" }));

  await waitFor(() => expect(request).toHaveBeenCalledWith(
    "/api/v1/runs/run-1/exports",
    { method: "POST", body: { format: "pdf", forced: false, pdf_dpi: 300 } }
  ));
});

it("offers a confirmed forced retry only for export_not_ready and preserves PDF options", async () => {
  let posts = 0;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") {
      posts += 1;
      if (posts === 1) throw new ApiError("页面质检尚未通过", 409, "export_not_ready");
      return snapshot("queued", null, { format: "pdf" }) as never;
    }
    return snapshot("running", null, { format: "pdf" }) as never;
  });
  renderPanel();
  fireEvent.change(screen.getByRole("spinbutton", { name: "PDF DPI" }), { target: { value: "300" } });
  fireEvent.click(screen.getByRole("button", { name: "生成 PDF" }));
  fireEvent.click(await screen.findByRole("button", { name: "仍要强制导出" }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认强制导出" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/runs/run-1/exports", { method: "POST", body: { format: "pdf", forced: true, pdf_dpi: 300, risk_acknowledgement: expect.any(String) } }));
});

it("does not post twice while the current export is active", async () => {
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued") as never;
    return snapshot("running") as never;
  });
  renderPanel();
  const button = screen.getByRole("button", { name: "生成 PPTX" });
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);

  expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("switches run scope without displaying or querying the previous export", async () => {
  sessionStorage.setItem("ppt-agent:active-export:run-1:pptx:default", "export-1");
  sessionStorage.setItem("ppt-agent:active-export:run-2:pptx:default", "export-2");
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    return path === "/api/v1/exports/export-2"
      ? snapshot("available", null, { id: "export-2", run_id: "run-2" }) as never
      : snapshot("available") as never;
  });
  const view = renderPanel("run-1");
  expect(await screen.findByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-1/download");

  view.rerenderRun("run-2");
  expect(screen.queryByRole("link", { name: "下载 PPTX" })?.getAttribute("href")).not.toBe("/api/v1/exports/export-1/download");
  expect(await screen.findByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-2/download");
  const oldCallsAtSwitch = request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1").length;
  await act(async () => { await Promise.resolve(); });
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1")).toHaveLength(oldCallsAtSwitch);
});

it("invalidates a removed run before a layout-timed old POST success can persist", async () => {
  const first = deferred<ExportSnapshot>();
  const storageKey = "ppt-agent:active-export:run-1:pptx:default";
  let markCommitted!: () => void;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (path, options) => {
    if (options?.method === "POST") return first.promise as never;
    return snapshot("available", null, { id: "export-a", run_id: "run-1" }) as never;
  });

  function SettleDuringReplacement({ runId, onCommit }: { runId: string; onCommit: () => void }) {
    useLayoutEffect(() => {
      if (runId === "run-2") {
        first.resolve(snapshot("available", null, { id: "export-a", run_id: "run-1" }));
        onCommit();
      }
    }, [onCommit, runId]);
    return null;
  }

  function Harness({ runId, onCommit }: { runId: string; onCommit: () => void }) {
    return <><ExportPanel runId={runId} /><SettleDuringReplacement runId={runId} onCommit={onCommit} /></>;
  }

  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const container = document.createElement("div");
  const root = createRoot(container);
  const noCommit = () => {};
  document.body.append(container);
  try {
    flushSync(() => root.render(<QueryClientProvider client={client}><Harness runId="run-1" onCommit={noCommit} /></QueryClientProvider>));
    await act(async () => {
      fireEvent.click(container.querySelector<HTMLButtonElement>("button")!);
    });
    await waitFor(() => expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));

    const committed = new Promise<void>((resolve) => { markCommitted = resolve; });
    root.render(<QueryClientProvider client={client}><Harness runId="run-2" onCommit={markCommitted} /></QueryClientProvider>);
    await committed;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(sessionStorage.getItem(storageKey)).toBeNull();
    expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-a")).toHaveLength(0);
  } finally {
    flushSync(() => root.unmount());
    container.remove();
  }
});

it("ignores an older same-run POST that succeeds after the latest intent", async () => {
  const first = deferred<ExportSnapshot>();
  const second = deferred<ExportSnapshot>();
  let postCount = 0;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (path, options) => {
    if (options?.method === "POST") {
      postCount += 1;
      return (postCount === 1 ? first.promise : second.promise) as never;
    }
    if (path === "/api/v1/exports/export-b") {
      return snapshot("available", null, { id: "export-b" }) as never;
    }
    return snapshot("available", null, { id: "export-a" }) as never;
  });
  const view = renderPanel("run-1");
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await waitFor(() => expect(postCount).toBe(1));
  view.rerenderRun("run-2");
  view.rerenderRun("run-1");
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await waitFor(() => expect(postCount).toBe(2));

  await act(async () => second.resolve(snapshot("available", null, { id: "export-b" })));
  expect(await screen.findByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-b/download");
  await act(async () => first.resolve(snapshot("available", null, { id: "export-a" })));

  expect(screen.getByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-b/download");
  expect(sessionStorage.getItem("ppt-agent:active-export:run-1:pptx:default")).toBe("export-b");
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-a")).toHaveLength(0);
});

it("ignores an older same-run POST error after the latest intent succeeds", async () => {
  const first = deferred<ExportSnapshot>();
  const second = deferred<ExportSnapshot>();
  let postCount = 0;
  vi.spyOn(apiClient, "request").mockImplementation(async (path, options) => {
    if (options?.method === "POST") {
      postCount += 1;
      return (postCount === 1 ? first.promise : second.promise) as never;
    }
    return snapshot("available", null, { id: "export-b" }) as never;
  });
  const view = renderPanel("run-1");
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await waitFor(() => expect(postCount).toBe(1));
  view.rerenderRun("run-2");
  view.rerenderRun("run-1");
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  await waitFor(() => expect(postCount).toBe(2));
  await act(async () => second.resolve(snapshot("available", null, { id: "export-b" })));
  await screen.findByRole("link", { name: "下载 PPTX" });

  await act(async () => first.reject(new ApiError("旧请求失败", 409, "old_failure")));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载 PPTX" })).toHaveAttribute("href", "/api/v1/exports/export-b/download");
});

it("posts again when the user explicitly regenerates an available export", async () => {
  sessionStorage.setItem("ppt-agent:active-export:run-1:pptx:default", "export-1");
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("available") as never;
    return snapshot("available") as never;
  });
  renderPanel();
  await screen.findByRole("link", { name: "下载 PPTX" });
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));

  await waitFor(() => expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));
});

it("posts again when the user explicitly retries a failed export", async () => {
  sessionStorage.setItem("ppt-agent:active-export:run-1:pptx:default", "export-1");
  let getCount = 0;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued") as never;
    getCount += 1;
    if (getCount === 1) {
      return snapshot("failed", { code: "export_failed", detail: "导出失败，可重新生成" }) as never;
    }
    return snapshot(getCount === 2 ? "running" : "available") as never;
  });
  renderPanel();
  expect(await screen.findByRole("alert")).toHaveTextContent("导出失败，可重新生成");
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));

  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText(/正在导出|导出排队中/)).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_500);
    await Promise.resolve();
  });
  expect(screen.getByText(/正在导出|导出排队中/)).toBeInTheDocument();
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1").length).toBeGreaterThanOrEqual(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_500);
    await Promise.resolve();
  });
  expect(screen.getByRole("link", { name: "下载 PPTX" })).toHaveAttribute(
    "href", "/api/v1/exports/export-1/download",
  );
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/exports/export-1").length).toBeGreaterThanOrEqual(3);
});

it("clears a missing recovered pointer and exposes regeneration", async () => {
  const key = "ppt-agent:active-export:run-1:pptx:default";
  sessionStorage.setItem(key, "missing-export");
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => {
    if (options?.method === "POST") return snapshot("queued", null, { id: "export-new" }) as never;
    throw new ApiError("导出记录不存在", 404, "export_not_found");
  });
  renderPanel();

  expect(await screen.findByRole("alert")).toHaveTextContent("导出记录不存在");
  await waitFor(() => expect(sessionStorage.getItem(key)).toBeNull());
  const button = screen.getByRole("button", { name: "生成 PPTX" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(request.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));
});

it("shows the safe API error when export creation is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("页面尚未全部通过质检。", 409));
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "生成 PPTX" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("页面尚未全部通过质检。");
});
