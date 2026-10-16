import { expect, test } from "@playwright/test";

type Json = Record<string, unknown>;

const configuredProvider = {
  mode: "deterministic",
  activation: "active",
  adapter_id: "volcengine_ark",
  adapter_status: { configured: false, backend: "workspace_secret_file" },
  multimodal_model: null,
  image_model: null,
  configuration_version: 1,
  pricing_overrides: {},
  legacy_configuration_detected: false,
  setup_required: false,
};

const emptyWorkspace = {
  project: { id: "project-1", name: "Fixture project" },
  runs: [],
  materials: [],
  deck_generations: [],
};

const catalog = [
  {
    adapter_id: "volcengine_ark",
    model_id: "doubao-seed-2-1-pro-260915",
    display_name: "Doubao Seed 2.1 Pro",
    category: "multimodal",
    lifecycle: "stable",
    capabilities: ["text.reasoning"],
    recommended: true,
    pricing: { currency: "CNY", version: "fixture" },
  },
  {
    adapter_id: "volcengine_ark",
    model_id: "doubao-seedream-5-0-pro-260628",
    display_name: "Doubao Seedream 5.0 Pro",
    category: "image",
    lifecycle: "stable",
    capabilities: ["image.generate"],
    recommended: true,
    pricing: { currency: "CNY", version: "fixture" },
  },
];

async function json(route: import("@playwright/test").Route, body: Json | Json[], status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installFixtureApi(
  page: import("@playwright/test").Page,
  options: { workspace?: Json; provider?: Json } = {},
) {
  await page.route("**/api/v1/session", (route) => json(route, { csrf_token: "fixture-csrf-token" }));
  await page.route("**/api/v1/workspace", (route) => json(route, { projects: [] }));
  await page.route("**/api/v1/projects/project-1/workspace", (route) => json(route, options.workspace ?? emptyWorkspace));
  await page.route("**/api/v1/provider-settings", (route) => json(route, options.provider ?? configuredProvider));
  await page.route("**/api/v1/model-catalog", (route) => json(route, catalog));
  await page.route("**/api/v1/settings/model-runtime", (route) => json(route, options.provider ?? configuredProvider));
  await page.route("**/api/v1/runs/*/events", (route) => route.fulfill({ status: 404, body: "" }));
}

test("creates, plans, approves, and renders a complete deterministic deck", async ({ page }) => {
  const projectName = `Browser E2E ${Date.now()}`;
  await page.goto("/");
  await page.getByRole("button", { name: /新建项目|创建第一个项目/ }).first().click();
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建并进入" }).click();

  await page.getByLabel("这份 PPT 要解决什么问题？").fill("为产品管理层制作一份 8 页的人工智能年度趋势报告，包含市场变化、用户价值、风险、路线图和下一步行动，风格专业简洁。受众是公司管理层，用于 15 分钟决策汇报。");
  await page.getByRole("button", { name: "开始规划" }).click();
  await expect(page.getByRole("heading", { name: "生成前，先确认完整规划" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".project-card")).toHaveCount(0);
  await page.getByRole("button", { name: "批准规划" }).click();
  await expect(page.getByRole("heading", { name: "规划已批准" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "开始生成 PPT" }).click();

  await expect(page.getByRole("heading", { name: "完整成品" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".slide-card")).toHaveCount(8, { timeout: 90_000 });
  await page.locator(".slide-card").first().click();
  await expect(page.getByRole("button", { name: "重生成本页" })).toBeVisible();
  await expect(page.getByRole("button", { name: "交付文字", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "设计 Prompt", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "局部修改", exact: true })).toBeVisible();
});

test("blocks cloud workspaces until provider configuration is complete", async ({ page }) => {
  await installFixtureApi(page, {
    provider: { ...configuredProvider, mode: "cloud", setup_required: true },
  });

  await page.goto("/projects/project-1");

  await expect(page.getByRole("alert")).toContainText("请先完成模型设置");
  await expect(page.getByRole("link", { name: "前往模型设置" })).toHaveAttribute("href", "/settings");
  await expect(page.getByRole("button", { name: "开始规划" })).toBeDisabled();
});

test("shows cancellation feedback across reload", async ({ page }) => {
  let runStatus = "running";
  let cancelCalls = 0;
  const workspace = {
    ...emptyWorkspace,
    runs: [{ id: "run-1", workflow: "production", status: runStatus, created_at: "2026-10-09T00:00:00Z" }],
  };
  await installFixtureApi(page, { workspace });
  await page.route("**/api/v1/projects/project-1/workspace", (route) => json(route, {
    ...workspace,
    runs: [{ ...workspace.runs[0], status: runStatus }],
  }));
  await page.route("**/api/v1/runs/run-1/snapshot", (route) => json(route, {
    run: { id: "run-1", status: runStatus }, steps: [], pages: [], interrupts: [], snapshot_seq: cancelCalls,
  }));
  await page.route("**/api/v1/runs/run-1/cancel", async (route) => {
    cancelCalls += 1;
    runStatus = "cancelled";
    await json(route, { run_id: "run-1", status: "cancelled", accepted: true }, 202);
  });

  await page.goto("/projects/project-1");
  await page.getByRole("button", { name: "取消任务" }).click();
  await page.getByRole("button", { name: "确认取消任务" }).click();
  await expect(page.getByRole("alert")).toContainText("任务已取消");

  // HTTP idempotency is covered by the API integration contract; this test
  // verifies the browser's actual confirmation and reload feedback path.
  expect(cancelCalls).toBe(1);

  await page.reload();
  await expect(page.getByRole("alert")).toContainText("任务已取消");
});

test("loads run trace only after the user opens run details", async ({ page }) => {
  let traceCalls = 0;
  const workspace = {
    ...emptyWorkspace,
    runs: [{ id: "run-1", workflow: "planning", status: "failed", created_at: "2026-10-09T00:00:00Z" }],
  };
  await installFixtureApi(page, { workspace });
  await page.route("**/api/v1/runs/run-1/snapshot", (route) => json(route, {
    run: { id: "run-1", status: "failed", error: { code: "invalid_request", provider_summary: { http_status: 400 } } },
    steps: [], pages: [], interrupts: [], snapshot_seq: 1,
  }));
  await page.route("**/api/v1/runs/run-1/trace", async (route) => {
    traceCalls += 1;
    await json(route, {
      run: { id: "run-1", project_id: "project-1", workflow_name: "deck_planning", workflow_version: 2, status: "failed", error: null },
      steps: [], events: [], operations: [],
    });
  });

  await page.goto("/projects/project-1");
  await expect(page.getByRole("heading", { name: "任务进度" })).toBeVisible();
  await expect(page.locator("#run-trace")).toHaveCount(0);
  expect(traceCalls).toBe(0);

  await page.getByRole("button", { name: "运行详情", exact: true }).click();
  await expect(page.locator("#run-trace")).toBeVisible();
  expect(traceCalls).toBe(1);
});

test("recovers an asynchronous export after refresh and only offers download when available", async ({ page }) => {
  const workspace = {
    ...emptyWorkspace,
    deck_generations: [{
      id: "deck-1", project_id: "project-1", planning_run_id: "plan-1", production_run_id: "production-1",
      generation: 1, status: "succeeded", source_generation_id: null, instruction: null,
      page_generation_ids: ["page-1"], created_at: "2026-10-09T00:00:00Z",
    }],
  };
  let exportStatus = "running";
  await installFixtureApi(page, { workspace });
  await page.route("**/api/v1/runs/production-1/production", (route) => json(route, {
    run_id: "production-1", status: "succeeded", pages: [{ id: "page-1", page_index: 1, status: "succeeded" }],
  }));
  await page.route("**/api/v1/pages/page-1", (route) => json(route, {
    id: "page-1", run_id: "production-1", page_index: 1, status: "succeeded", stage: "complete", error: null,
    current_generation: {
      id: "page-gen-1", generation: 1, status: "succeeded", source_generation_id: null,
      delivery_text: [], design_prompt: "", local_edit_prompt: null, spec: {}, quality: { passed: true, issues: [] },
      page_image_url: null, thumbnail_url: null, created_at: "2026-10-09T00:00:00Z",
    },
  }));
  await page.route("**/api/v1/runs/production-1/exports", (route) => json(route, {
    id: "export-1", project_id: "project-1", run_id: "production-1", format: "pdf", status: "queued",
    artifact_id: null, error: null, attempt_count: 0, started_at: null, finished_at: null,
  }, 201));
  await page.route("**/api/v1/exports/export-1", (route) => json(route, {
    id: "export-1", project_id: "project-1", run_id: "production-1", format: "pdf", status: exportStatus,
    artifact_id: exportStatus === "available" ? "artifact-1" : null, error: null, attempt_count: 1,
    started_at: "2026-10-09T00:00:00Z", finished_at: exportStatus === "available" ? "2026-10-09T00:01:00Z" : null,
  }));

  await page.goto("/projects/project-1");
  await page.getByRole("button", { name: "生成 PDF" }).click();
  await expect(page.getByRole("status")).toContainText("正在导出");
  await expect(page.getByRole("link", { name: "下载 PDF" })).not.toBeVisible();

  await page.reload();
  await expect(page.getByRole("status")).toContainText("正在导出");
  await expect(page.getByRole("link", { name: "下载 PDF" })).not.toBeVisible();

  exportStatus = "available";
  await page.reload();
  await expect(page.getByRole("link", { name: "下载 PDF" })).toBeVisible();
});

test("exposes provider read failures as an accessible alert", async ({ page }) => {
  await installFixtureApi(page);
  await page.route("**/api/v1/provider-settings", (route) => json(route, { detail: "provider settings unavailable" }, 503));

  await page.goto("/projects/project-1");

  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("无法读取模型设置");
  await expect(alert).toHaveAttribute("aria-live", "polite");
});

test("traps shared Dialog focus and restores the trigger after Escape", async ({ page }) => {
  await installFixtureApi(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: /新建项目/ }).first();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "给这份演示起个名字" });
  await expect(dialog).toBeVisible();
  await expect(page.locator("#project-name")).toBeFocused();

  await page.locator("#project-name").press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
  await dialog.getByRole("button", { name: "取消" }).press("Tab");
  await expect(page.locator("#project-name")).toBeFocused();
  await page.locator("#project-name").press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test("guides an empty workspace through settings and deterministic mode", async ({ page }) => {
  await installFixtureApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从资料到成品，只需三步" })).toBeVisible();
  await expect(page.getByText(/确定性模式无需 API Key/)).toBeVisible();
  await expect(page.getByRole("link", { name: "检查模型设置" })).toHaveAttribute("href", "/settings");
});
