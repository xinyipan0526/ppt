import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import { ClarificationForm } from "./ClarificationForm";
import { PlanReview } from "./PlanReview";

it("shows the safe API error when clarification submission is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("澄清表单已过期。", 409));
  render(<QueryClientProvider client={new QueryClient()}><ClarificationForm runId="run-1" interrupt={{ id: "interrupt-1", values: { version: 2, form_schema: { fields: [{ name: "goal", question: "目标", type: "text", recommended_value: "季度复盘" }] } } }} onDone={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "提交并继续" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("澄清表单已过期。");
});

it("shows the safe API error when plan review cancellation is rejected", async () => {
  vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    if (path.endsWith("/plan-detail")) return { run_id: "run-1", brief: null, outline: null, design_system: null, pages: [], review_ready: true, bundle_ready: true };
    throw new ApiError("规划复核版本已过期。", 409);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PlanReview projectId="project-1" runId="run-1" interrupt={{ id: "interrupt-1", values: { version: 2 } }} onApproved={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("规划复核版本已过期。");
});
it("blocks paid clarification resume when provider setup is required", () => {
  render(<QueryClientProvider client={new QueryClient()}><ClarificationForm runId="run-1" interrupt={{ id: "interrupt-1", values: { version: 2, form_schema: { fields: [{ name: "goal", question: "goal", type: "text", recommended_value: "quarterly review" }] } } }} onDone={() => undefined} disabled /></QueryClientProvider>);
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getAllByRole("button")[0]).toBeDisabled();
});

it("blocks paid plan approval and revision when provider setup is required", async () => {
  vi.spyOn(apiClient, "request").mockResolvedValue({ run_id: "run-1", brief: null, outline: null, design_system: null, pages: [], review_ready: true, bundle_ready: true });
  render(<QueryClientProvider client={new QueryClient()}><PlanReview projectId="project-1" runId="run-1" interrupt={{ id: "interrupt-1", values: { version: 2 } }} onApproved={() => undefined} disabled /></QueryClientProvider>);
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "make it clearer" } });
  const buttons = await screen.findAllByRole("button");
  expect(buttons.filter((button) => button.hasAttribute("disabled"))).toHaveLength(2);
});
