import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { DeckGeneration } from "../../api/contracts";
import { DeckGenerationPicker } from "./DeckGenerationPicker";

const generations: DeckGeneration[] = [
  { id: "deck-2", project_id: "project-1", planning_run_id: "plan-2", production_run_id: "run-2", generation: 2, status: "succeeded", source_generation_id: "deck-1", instruction: null, page_generation_ids: [], created_at: "2026-10-14T02:00:00Z" },
  { id: "deck-1", project_id: "project-1", planning_run_id: "plan-1", production_run_id: "run-1", generation: 1, status: "succeeded", source_generation_id: null, instruction: null, page_generation_ids: [], created_at: "2026-10-14T01:00:00Z" },
];

it("labels history and restores with optimistic generation protection", async () => {
  const request = vi.spyOn(apiClient, "request").mockResolvedValue({ ...generations[0], id: "deck-3", generation: 3 });
  const selected: string[] = [];
  render(<QueryClientProvider client={new QueryClient()}><DeckGenerationPicker projectId="project-1" generations={generations} selectedId="deck-1" onSelect={(id) => selected.push(id)} onRestored={(deck) => selected.push(deck.id)} /></QueryClientProvider>);
  expect(screen.getByRole("option", { name: /版本 1 · 历史版本/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "恢复为新版本" }));
  fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/projects/project-1/deck-generations/deck-1/restore", expect.objectContaining({ body: { expected_generation: 2 } })));
  expect(selected).toContain("deck-3");
});
