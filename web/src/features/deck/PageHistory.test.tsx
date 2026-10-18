import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import { PageHistory } from "./PageHistory";

it("shows the safe API error when restoring a historical page is rejected", async () => {
  vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    if (path.endsWith("/history")) return [{ id: "previous", generation: 1, status: "succeeded", source_generation_id: null, delivery_text: [], design_prompt: "", local_edit_prompt: null, spec: null, quality: null, page_image_url: "/page.png", thumbnail_url: null, created_at: "2026-10-01" }];
    throw new ApiError("当前页面版本已更新。", 409);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PageHistory pageId="page-1" currentId="current" /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "恢复为新版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("当前页面版本已更新。");
});
