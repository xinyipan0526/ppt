import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import { ProviderSetup } from "./ProviderSetup";

it("submits keys once, clears fields, and never writes browser storage", async () => {
  const status = { mode: "deterministic", activation: "active", pricing_version: "v1", providers: {
    deepseek: { configured: false, backend: "workspace_secret_file", masked_suffix: null },
    seedream: { configured: false, backend: "workspace_secret_file", masked_suffix: null },
    qwen: { configured: false, backend: "workspace_secret_file", masked_suffix: null }
  } } as const;
  const request = vi.spyOn(apiClient, "request").mockImplementation(async (_path, options) => options?.method === "PUT" ? { ...status, mode: "cloud" } : status);
  const storage = vi.spyOn(Storage.prototype, "setItem");
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProviderSetup /></QueryClientProvider>);
  await screen.findByText("确定性模式");
  const secrets = ["deepseek-browser-secret", "seedream-browser-secret", "qwen-browser-secret"];
  const inputs = screen.getAllByPlaceholderText("留空则保持现有密钥") as HTMLInputElement[];
  inputs.forEach((input, index) => fireEvent.change(input, { target: { value: secrets[index] } }));
  fireEvent.click(screen.getByRole("button", { name: "保存并启用" }));
  await screen.findByText(/设置已安全保存/);
  await waitFor(() => inputs.forEach((input) => expect(input.value).toBe("")));
  expect(storage).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain("browser-secret");
  expect(JSON.stringify(request.mock.calls)).toContain("deepseek-browser-secret");
});
