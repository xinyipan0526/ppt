import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PageDetail } from "../../api/contracts";
import { PageInspector } from "./PageInspector";

const page: PageDetail = { id: "page-1", run_id: "run-1", page_index: 1, status: "succeeded", stage: "validation", error: null, current_generation: { id: "gen-1", generation: 1, status: "succeeded", source_generation_id: null, delivery_text: [{ id: "title", role: "title", text: "增长引擎" }], design_prompt: "深蓝几何空间", local_edit_prompt: null, spec: null, quality: null, page_image_url: "/image", thumbnail_url: "/thumb", created_at: "2026-10-01" } };

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
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<QueryClientProvider client={new QueryClient()}><PageInspector page={page} onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "重生成本页" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/pages/page-1/regenerate", expect.objectContaining({ method: "POST" })));
});
