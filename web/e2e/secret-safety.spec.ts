import { expect, test } from "@playwright/test";

test("Ark secret leaves no browser-persistent or rendered trace", async ({ page }) => {
  const secret = "ark-e2e-secret";
  const submittedBodies: string[] = [];
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));

  const catalog = [
    {
      adapter_id: "volcengine_ark",
      model_id: "doubao-seed-2-1-pro-260915",
      display_name: "Doubao Seed 2.1 Pro",
      category: "multimodal",
      lifecycle: "stable",
      capabilities: ["text.reasoning", "vision.analyze", "vision.locate", "ocr.recognize"],
      recommended: true,
      pricing: { currency: "CNY", version: "official-cn-2026-10-03" },
    },
    {
      adapter_id: "volcengine_ark",
      model_id: "doubao-seed-2-1-lite-260915",
      display_name: "Doubao Seed 2.1 Lite",
      category: "multimodal",
      lifecycle: "stable",
      capabilities: ["text.reasoning", "vision.analyze", "vision.locate", "ocr.recognize"],
      recommended: false,
      pricing: { currency: "CNY", version: "official-cn-2026-10-03" },
    },
    {
      adapter_id: "volcengine_ark",
      model_id: "doubao-seedream-5-0-pro-260628",
      display_name: "Doubao Seedream 5.0 Pro",
      category: "image",
      lifecycle: "stable",
      capabilities: ["image.generate", "image.edit"],
      recommended: true,
      pricing: { currency: "CNY", version: "official-cn-2026-10-03" },
    },
  ];
  const savedSnapshot = {
    mode: "cloud",
    activation: "active",
    adapter_id: "volcengine_ark",
    adapter_status: { configured: true, backend: "workspace_secret_file" },
    multimodal_model: catalog[0],
    image_model: catalog[2],
    configuration_version: 2,
    pricing_overrides: {},
    legacy_configuration_detected: false,
  };
  const emptySnapshot = {
    ...savedSnapshot,
    mode: "deterministic",
    adapter_status: { configured: false, backend: "workspace_secret_file" },
    multimodal_model: null,
    image_model: null,
    configuration_version: 1,
  };

  await page.route("**/api/v1/model-catalog", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(catalog),
  }));
  await page.route("**/api/v1/settings/model-runtime", async (route) => {
    if (route.request().method() === "PUT") {
      submittedBodies.push(route.request().postData() ?? "");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(savedSnapshot) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptySnapshot) });
  });
  await page.route("**/api/v1/adapters/volcengine_ark/test", async (route) => {
    submittedBodies.push(route.request().postData() ?? "");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
  });

  await page.goto("/settings");
  const input = page.getByLabel("火山方舟 API Key");
  await expect(input).toHaveCount(1);
  await expect(page.getByLabel("多模态语言模型")).toHaveValue("doubao-seed-2-1-pro-260915");
  await expect(page.getByLabel("图片生成模型")).toHaveValue("doubao-seedream-5-0-pro-260628");
  await input.fill(secret);
  await page.getByRole("button", { name: "测试并启用" }).click();
  await expect(page.getByText(/设置已安全保存/)).toBeVisible();
  await expect(input).toHaveValue("");

  expect(submittedBodies).toHaveLength(2);
  expect(submittedBodies.every((body) => body.includes(secret))).toBe(true);
  expect(submittedBodies.every((body) => body.includes("doubao-seed-2-1-pro-260915"))).toBe(true);
  expect(submittedBodies.every((body) => body.includes("doubao-seedream-5-0-pro-260628"))).toBe(true);
  const browserState = await page.evaluate(() => ({
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    url: location.href,
    html: document.documentElement.textContent ?? "",
  }));
  expect(JSON.stringify(browserState)).not.toContain(secret);
  expect(consoleLines.join("\n")).not.toContain(secret);
});
