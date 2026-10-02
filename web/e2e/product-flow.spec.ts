import { expect, test } from "@playwright/test";

test("creates, plans, approves, and renders a complete deterministic deck", async ({ page }) => {
  const projectName = `Browser E2E ${Date.now()}`;
  await page.goto("/");
  await page.getByRole("button", { name: /新建项目|创建第一个项目/ }).first().click();
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建并进入" }).click();

  await page.getByLabel("这份 PPT 要解决什么问题？").fill("为产品管理层制作一份 8 页的人工智能年度趋势报告，包含市场变化、用户价值、风险、路线图和下一步行动，风格专业简洁。受众是公司管理层，用于 15 分钟决策汇报。");
  await page.getByRole("button", { name: "开始规划" }).click();
  await expect(page.getByRole("heading", { name: "生成前，先确认每一页" })).toBeVisible({ timeout: 60_000 });
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
