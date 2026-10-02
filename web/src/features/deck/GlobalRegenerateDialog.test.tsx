import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
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
