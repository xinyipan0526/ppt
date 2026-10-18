import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import { MaterialPanel } from "./MaterialPanel";

it("shows the safe API error when upload is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("文件超过项目限额。", 413));
  const { container } = render(<QueryClientProvider client={new QueryClient()}><MaterialPanel projectId="project-1" materials={[]} selected={[]} onSelected={() => undefined} /></QueryClientProvider>);
  const input = container.querySelector('input[type="file"]')!;
  const file = new File(["source"], "brief.md", { type: "text/markdown" });
  fireEvent.change(input, { target: { files: { 0: file, length: 1, item: () => file } } });
  expect(await screen.findByRole("alert")).toHaveTextContent("文件超过项目限额。");
});

it("clears material selection only after confirmed deletion succeeds", async () => {
  const onSelected = vi.fn();
  const request = vi.spyOn(apiClient, "request").mockResolvedValue(undefined);
  render(<QueryClientProvider client={new QueryClient()}><MaterialPanel projectId="project-1" materials={[{ id: "material-1", filename: "brief.md", media_type: "text/markdown", status: "ready", version: 2, created_at: "2026-10-01" }]} selected={["material-1"]} onSelected={onSelected} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "删除素材 brief.md" }));
  expect(onSelected).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认删除素材" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/projects/project-1/materials/material-1", { method: "DELETE" }));
  expect(onSelected).toHaveBeenCalledWith([]);
});

it("shows the safe API error when a failed material retry is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("素材版本已更新，请刷新。", 409));
  render(<QueryClientProvider client={new QueryClient()}><MaterialPanel projectId="project-1" materials={[{ id: "material-1", filename: "brief.md", media_type: "text/markdown", status: "failed", version: 2, created_at: "2026-10-01" }]} selected={[]} onSelected={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("素材版本已更新，请刷新。");
});
