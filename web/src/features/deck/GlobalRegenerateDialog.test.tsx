import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import type { DeckGeneration } from "../../api/contracts";
import { GlobalRegenerateDialog } from "./GlobalRegenerateDialog";

it("requires an explicit cost confirmation before global regeneration", async () => {
  const deck: DeckGeneration = { id: "deck-2", project_id: "p1", planning_run_id: "plan", production_run_id: "prod", generation: 2, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-01" };
  vi.spyOn(apiClient, "request").mockResolvedValue({ run_id: "new-run" });
  render(<QueryClientProvider client={new QueryClient()}><GlobalRegenerateDialog projectId="p1" deck={deck} onClose={() => undefined} onCreated={() => undefined} /></QueryClientProvider>);
  const submit = screen.getByRole("button", { name: "创建新版本" });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByPlaceholderText(/整体更克制/), { target: { value: "统一为深蓝风格" } });
  fireEvent.click(screen.getByRole("checkbox"));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  await waitFor(() => expect(apiClient.request).toHaveBeenCalledWith("/api/v1/projects/p1/deck-generations/deck-2/regenerate", expect.objectContaining({ body: expect.objectContaining({ expected_generation: 2 }) })));
});

it("shows the safe API error when global regeneration is rejected", async () => {
  const deck: DeckGeneration = { id: "deck-2", project_id: "p1", planning_run_id: "plan", production_run_id: "prod", generation: 2, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-01" };
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("当前项目正在生成，请稍后重试。", 409));
  render(<QueryClientProvider client={new QueryClient()}><GlobalRegenerateDialog projectId="p1" deck={deck} onClose={() => undefined} onCreated={() => undefined} /></QueryClientProvider>);

  fireEvent.change(screen.getByPlaceholderText(/整体更克制/), { target: { value: "统一为深蓝风格" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "创建新版本" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("当前项目正在生成，请稍后重试。");
});
it("blocks an already-open global regeneration dialog when provider setup becomes required", () => {
  const deck: DeckGeneration = { id: "deck-2", project_id: "p1", planning_run_id: "plan", production_run_id: "prod", generation: 2, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-01" };
  render(<QueryClientProvider client={new QueryClient()}><GlobalRegenerateDialog projectId="p1" deck={deck} onClose={() => undefined} onCreated={() => undefined} disabled /></QueryClientProvider>);
  expect(document.querySelector("button.button.primary") as HTMLButtonElement).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
});
