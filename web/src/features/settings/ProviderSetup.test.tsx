import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import { ProviderSetup } from "./ProviderSetup";

const catalog = [
  { adapter_id: "volcengine_ark", model_id: "doubao-seed-2-1-pro-260915", display_name: "Doubao Seed 2.1 Pro", category: "multimodal", lifecycle: "stable", capabilities: [], recommended: true, pricing: { currency: "CNY", version: "v1" } },
  { adapter_id: "volcengine_ark", model_id: "doubao-seed-2-1-lite-260915", display_name: "Doubao Seed 2.1 Lite", category: "multimodal", lifecycle: "stable", capabilities: [], recommended: false, pricing: { currency: "CNY", version: "v1" } },
  { adapter_id: "volcengine_ark", model_id: "doubao-seedream-5-0-pro-260628", display_name: "Doubao Seedream 5.0 Pro", category: "image", lifecycle: "stable", capabilities: [], recommended: true, pricing: { currency: "CNY", version: "v1" } },
] as const;

const status = {
  mode: "deterministic", activation: "active", adapter_id: "volcengine_ark",
  adapter_status: { configured: false, backend: "workspace_secret_file" },
  multimodal_model: null, image_model: null, configuration_version: 1,
  pricing_overrides: {}, legacy_configuration_detected: false,
} as const;

it("uses one key and exact catalog model IDs, then clears the key", async () => {
  const calls: Array<[string, unknown]> = [];
  vi.spyOn(apiClient, "request").mockImplementation(async (path, options) => {
    calls.push([path, options?.body]);
    if (path === "/api/v1/model-catalog") return catalog;
    if (path === "/api/v1/settings/model-runtime" && options?.method === "PUT") {
      return { ...status, mode: "cloud", adapter_status: { configured: true, backend: "workspace_secret_file" }, multimodal_model: catalog[0], image_model: catalog[2] };
    }
    if (path === "/api/v1/adapters/volcengine_ark/test") return { status: "ok" };
    return status;
  });
  const storage = vi.spyOn(Storage.prototype, "setItem");
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProviderSetup /></QueryClientProvider>);

  const key = await screen.findByLabelText("火山方舟 API Key") as HTMLInputElement;
  const multimodal = screen.getByLabelText("多模态语言模型") as HTMLSelectElement;
  const image = screen.getByLabelText("图片生成模型") as HTMLSelectElement;
  expect(screen.getAllByLabelText(/API Key/)).toHaveLength(1);
  await waitFor(() => expect(multimodal.value).toBe("doubao-seed-2-1-pro-260915"));
  expect(image.value).toBe("doubao-seedream-5-0-pro-260628");
  expect(screen.getByRole("option", { name: /Doubao Seed 2.1 Lite/ })).toHaveValue("doubao-seed-2-1-lite-260915");

  fireEvent.change(key, { target: { value: "ark-browser-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并启用" }));
  await screen.findByText(/设置已安全保存/);
  await waitFor(() => expect(key.value).toBe(""));

  const mutationBodies = calls.filter(([, body]) => body).map(([, body]) => body);
  expect(mutationBodies).toHaveLength(2);
  expect(mutationBodies).toEqual([
    { ark_api_key: "ark-browser-secret", multimodal_model_id: "doubao-seed-2-1-pro-260915", image_model_id: "doubao-seedream-5-0-pro-260628" },
    { ark_api_key: "ark-browser-secret", multimodal_model_id: "doubao-seed-2-1-pro-260915", image_model_id: "doubao-seedream-5-0-pro-260628" },
  ]);
  expect(storage).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain("ark-browser-secret");
});

it("keeps advanced pricing optional and can reuse an existing credential", async () => {
  vi.spyOn(apiClient, "request").mockImplementation(async (path) => {
    if (path === "/api/v1/model-catalog") return catalog;
    return { ...status, mode: "cloud", adapter_status: { configured: true, backend: "workspace_secret_file" }, multimodal_model: catalog[1], image_model: catalog[2] };
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProviderSetup /></QueryClientProvider>);

  const multimodal = await screen.findByLabelText("多模态语言模型") as HTMLSelectElement;
  await waitFor(() => expect(multimodal.value).toBe("doubao-seed-2-1-lite-260915"));
  expect(screen.getByText("高级设置（可选）")).toBeInTheDocument();
  expect(screen.getByLabelText("火山方舟 API Key")).not.toBeRequired();
});
