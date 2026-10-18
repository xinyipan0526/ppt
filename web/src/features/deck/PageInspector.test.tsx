import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import type { PageDetail } from "../../api/contracts";
import { PageInspector } from "./PageInspector";

const page: PageDetail = { id: "page-1", run_id: "run-1", page_index: 1, status: "succeeded", stage: "validation", error: null, pending_edit: null, current_generation: { id: "gen-1", generation: 1, status: "succeeded", source_generation_id: null, delivery_text: [{ id: "title", role: "title", text: "增长引擎" }], design_prompt: "深蓝几何空间", local_edit_prompt: null, spec: null, quality: null, page_image_url: "/image", thumbnail_url: "/thumb", created_at: "2026-10-01" } };

it("separates delivery text, design prompt, and local edit prompt", async () => {
  vi.spyOn(apiClient, "request").mockResolvedValue({ status: "succeeded" });
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);
  expect(screen.getByDisplayValue("增长引擎")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "设计 Prompt" }));
  expect(screen.getByDisplayValue("深蓝几何空间")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "局部修改" }));
  fireEvent.change(screen.getByPlaceholderText(/把标题改成/), { target: { value: "标题更短" } });
  fireEvent.click(screen.getByRole("button", { name: "执行修改" }));
  await waitFor(() => expect(apiClient.request).toHaveBeenCalledWith("/api/v1/pages/page-1/edits", expect.objectContaining({ method: "POST" })));
});

it("requires confirmation before regenerating one page", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue({ status: "succeeded" });
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "重生成本页" }));
  expect(screen.getByRole("dialog", { name: "确认重生成本页" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认重生成" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/pages/page-1/regenerate", expect.objectContaining({ method: "POST" })));
});

it("shows the safe API error when a local edit is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("页面已被更新，请刷新后重试。", 409));
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);

  fireEvent.click(screen.getByRole("button", { name: "局部修改" }));
  fireEvent.change(screen.getByPlaceholderText(/把标题改成/), { target: { value: "标题更短" } });
  fireEvent.click(screen.getByRole("button", { name: "执行修改" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("页面已被更新，请刷新后重试。");
});

it("shows the safe API error when page regeneration is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("页面生成请求已过期。", 409));
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "重生成本页" }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认重生成" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("页面生成请求已过期。");
});

it("separates local and paid visual edits", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue({ status: "succeeded" });
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "局部修改" }));
  fireEvent.change(screen.getByPlaceholderText(/把标题改成/), { target: { value: "把背景换成蓝色山脉" } });
  fireEvent.click(screen.getByRole("button", { name: /修改视觉图片/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认视觉修改" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/pages/page-1/edits", expect.objectContaining({ body: expect.objectContaining({ resample_visual: true }) })));
});

it("resumes a persisted clarification after refresh", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue({ status: "queued" });
  const pending: PageDetail = { ...page, pending_edit: { id: "edit-7", status: "awaiting_clarification", kind: "visual", clarification_question: "要修改哪一处视觉？", target_box: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 } } };
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={pending} onClose={() => undefined} /></QueryClientProvider>);
  expect(screen.getByText("要修改哪一处视觉？")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("补充说明"), { target: { value: "右侧插图" } });
  fireEvent.click(screen.getByRole("button", { name: "继续修改" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/page-edits/edit-7/resume", { method: "POST", body: { instruction: "右侧插图", target_box: pending.pending_edit?.target_box } }));
});
it("blocks page regeneration and visual edit submission when provider setup is required", () => {
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} disabled /></QueryClientProvider>);
  const regenerate = document.querySelector("button.button.danger") as HTMLButtonElement;
  expect(regenerate).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "局部修改" }));
  expect(screen.getByRole("button", { name: "执行修改" })).toBeDisabled();
});

it("shows the persisted quality report in a dedicated tab", () => {
  const qualityPage: PageDetail = { ...page, current_generation: { ...page.current_generation, quality: { passed: false, issues: [{ code: "layout_overflow", severity: "error", summary: "正文超出安全区" }] } } };
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={qualityPage} onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "质量检查" }));
  expect(screen.getByText("正文超出安全区")).toBeInTheDocument();
  expect(screen.getByText("质检未通过")).toBeInTheDocument();
});
