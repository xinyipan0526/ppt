import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "../../api/client";
import { PlanningInput } from "./PlanningInput";

afterEach(() => vi.restoreAllMocks());

it("shows the safe API error beside the planning input when creation is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("模型配置尚未完成", 400));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PlanningInput projectId="project-1" materialIds={[]} onCreated={() => {}} />
    </QueryClientProvider>,
  );

  fireEvent.change(screen.getByLabelText("这份 PPT 要解决什么问题？"), { target: { value: "季度复盘" } });
  fireEvent.click(screen.getByRole("button", { name: "开始规划" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("模型配置尚未完成");
});

it("keeps planning input visible but blocks submission when paid actions are unavailable", () => {
  const request = vi.spyOn(apiClient, "request");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PlanningInput projectId="project-1" materialIds={["material-1"]} onCreated={() => {}} disabled />
    </QueryClientProvider>,
  );

  expect(screen.getByLabelText("这份 PPT 要解决什么问题？")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "开始规划" }));
  expect(screen.getByRole("button", { name: "开始规划" })).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
});
