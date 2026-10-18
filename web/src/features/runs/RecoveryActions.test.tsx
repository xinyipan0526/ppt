import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";
import { RecoveryActions } from "./RecoveryActions";

it("shows the safe API error when recovery authorization is rejected", async () => {
  vi.spyOn(apiClient, "request").mockRejectedValue(new ApiError("恢复授权已过期，请刷新任务。", 409));
  render(<QueryClientProvider client={new QueryClient()}><RecoveryActions runId="run-1" onRecovered={() => undefined} /></QueryClientProvider>);

  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "授权恢复" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("恢复授权已过期，请刷新任务。");
});

it("blocks recovery authorization when paid actions are unavailable", () => {
  const request = vi.spyOn(apiClient, "request");
  render(<QueryClientProvider client={new QueryClient()}><RecoveryActions runId="run-1" onRecovered={() => undefined} disabled /></QueryClientProvider>);

  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(screen.getByRole("button", { name: "授权恢复" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "授权恢复" }));
  expect(request).not.toHaveBeenCalled();
});

it("blocks local retry until every uncertain remote attempt is resolved", () => {
  const request = vi.spyOn(apiClient, "request");
  render(<QueryClientProvider client={new QueryClient()}><RecoveryActions runId="run-1" unresolvedRemoteCount={2} onRecovered={() => undefined} /></QueryClientProvider>);
  expect(screen.getByText("还有 2 个远端操作需要先处理")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "授权恢复" })).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
});
