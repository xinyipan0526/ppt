import { describe, expect, it } from "vitest";
import { getStatusPresentation } from "./statusPresentation";

describe("getStatusPresentation", () => {
  it.each([
    ["queued", "排队中"], ["running", "正在运行"], ["waiting_for_user", "等待确认"],
    ["waiting_for_retry", "等待自动重试"], ["recovery_required", "需要人工恢复"],
    ["cancel_requested", "正在取消"], ["cancelled", "已取消"],
    ["failed", "失败"], ["succeeded", "已完成"],
  ])("maps %s to a Chinese product label", (status, label) => {
    expect(getStatusPresentation(status).label).toBe(label);
  });

  it("uses a bounded neutral fallback for an unknown status", () => {
    expect(getStatusPresentation("future_state")).toMatchObject({ label: "未知状态", tone: "neutral" });
  });
});
