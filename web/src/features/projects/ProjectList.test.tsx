import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ApiError, apiClient } from "../../api/client";
import { ProjectList } from "./ProjectList";

it("shows the safe API error when project creation is rejected", async () => {
  vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    if (path === "/api/v1/workspace") return { projects: [] };
    throw new ApiError("项目名称已存在。", 409);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ProjectList /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /新建项目/ }));
  fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "年度复盘" } });
  fireEvent.click(screen.getByRole("button", { name: "创建并进入" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("项目名称已存在。");
});

it("guides an empty workspace to model settings without blocking deterministic use", async () => {
  vi.spyOn(apiClient, "request").mockResolvedValue({ projects: [] });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ProjectList /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("link", { name: "检查模型设置" })).toHaveAttribute("href", "/settings");
  expect(screen.getByText(/确定性模式无需 API Key/)).toBeInTheDocument();
});

it("deletes a project only after confirmation", async () => {
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (path, options) => {
    if (path === "/api/v1/workspace") return { projects: [{ id: "project-1", name: "年度复盘", created_at: "2026-10-01", latest_run: null, material_count: 0, deck_generation_count: 0 }] };
    if (path === "/api/v1/projects/project-1" && options?.method === "DELETE") return undefined;
    throw new Error(`unexpected ${path}`);
  });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ProjectList /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "删除项目 年度复盘" }));
  expect(request).not.toHaveBeenCalledWith("/api/v1/projects/project-1", expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "确认删除项目" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/projects/project-1", { method: "DELETE" }));
});
