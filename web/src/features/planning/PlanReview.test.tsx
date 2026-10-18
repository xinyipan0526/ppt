import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import { PlanReview } from "./PlanReview";

it("renders the complete typed plan before approval", async () => {
  vi.spyOn(apiClient, "request").mockResolvedValue({
    run_id: "run-1", review_ready: true, bundle_ready: true,
    brief: { topic: "季度增长", purpose: "复盘", audience: "管理层", language: "zh-CN", tone: "克制", page_count: 1, aspect_ratio: "16:9", budget_minor_units: 2000, quality: "high" },
    outline: { narrative: "从现状到行动", pages: [{ page_index: 1, objective: "建立共识", section: "开场" }] },
    design_system: { color_palette: ["#3157ff"], typography: { heading: "Inter" }, visual_style: "编辑式", image_style: "抽象几何", spacing_scale: [4, 8], prohibited: ["拥挤"] },
    pages: [{ page_index: 1, objective: "建立共识", page_type: "cover", title: "季度增长", body_blocks: [], layout_intent: "居中", visual_requirements: [], source_refs: [], acceptance_criteria: [] }],
  });
  render(<QueryClientProvider client={new QueryClient()}><PlanReview projectId="project-1" runId="run-1" interrupt={{ id: "i-1", values: { version: 1 } }} onApproved={() => undefined} /></QueryClientProvider>);
  expect(await screen.findByRole("heading", { name: "季度增长" })).toBeInTheDocument();
  expect(screen.getByText("从现状到行动")).toBeInTheDocument();
  expect(screen.getByText("编辑式")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准规划" })).toBeEnabled();
});

it("disables approval and reports plan load failure", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new Error("offline"));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PlanReview projectId="project-1" runId="run-1" interrupt={{ id: "i-1", values: { version: 1 } }} onApproved={() => undefined} /></QueryClientProvider>);
  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取规划");
  expect(screen.getByRole("button", { name: "批准规划" })).toBeDisabled();
});
