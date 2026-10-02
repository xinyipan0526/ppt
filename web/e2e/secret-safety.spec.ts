import { expect, test } from "@playwright/test";

test("provider secrets leave no browser-persistent or rendered trace", async ({ page }) => {
  const secrets = ["deepseek-e2e-secret", "seedream-e2e-secret", "qwen-e2e-secret"];
  let submittedBody = "";
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  const safeSnapshot = {
    mode: "cloud",
    activation: "active",
    pricing_version: "official-current",
    seedream_result_hosts: ["ark-content-generation-cn-beijing.tos-cn-beijing.volces.com"],
    pricing_currency: "CNY",
    deepseek_input_per_million: 1,
    deepseek_output_per_million: 1,
    seedream_per_image: 1,
    qwen_input_per_million: 1,
    qwen_output_per_million: 1,
    providers: {
      deepseek: { configured: true, backend: "workspace_secret_file", masked_suffix: "cret" },
      seedream: { configured: true, backend: "workspace_secret_file", masked_suffix: "cret" },
      qwen: { configured: true, backend: "workspace_secret_file", masked_suffix: "cret" },
    },
  };
  await page.route("**/api/v1/settings/providers", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(safeSnapshot) });
      return;
    }
    submittedBody = route.request().postData() ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(safeSnapshot),
    });
  });

  await page.goto("/settings");
  const inputs = page.getByPlaceholder("留空则保持现有密钥");
  await expect(inputs).toHaveCount(3);
  for (let index = 0; index < secrets.length; index += 1) await inputs.nth(index).fill(secrets[index]);
  await page.getByRole("button", { name: "保存并启用" }).click();
  await expect(page.getByText(/设置已安全保存/)).toBeVisible();
  for (let index = 0; index < secrets.length; index += 1) await expect(inputs.nth(index)).toHaveValue("");

  expect(submittedBody).toContain(secrets[0]);
  const browserState = await page.evaluate(() => ({
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    url: location.href,
    html: document.documentElement.textContent ?? "",
  }));
  for (const secret of secrets) {
    expect(JSON.stringify(browserState)).not.toContain(secret);
    expect(consoleLines.join("\n")).not.toContain(secret);
  }
});
