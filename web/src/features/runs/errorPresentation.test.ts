import { describe, expect, it } from "vitest";
import { formatSafeRunError } from "./errorPresentation";

describe("formatSafeRunError", () => {
  it("maps known safe error codes and only appends allowlisted provider details", () => {
    expect(formatSafeRunError({
      code: "invalid_request",
      provider_summary: { http_status: 400, provider_error_type: "invalid_request" },
    })).toBe("模型请求参数不兼容，请检查模型配置或更新服务。 (invalid_request, HTTP 400)");
  });

  it("uses a bounded fallback for an unknown code", () => {
    expect(formatSafeRunError({ code: "future_code" })).toBe("任务失败：future_code");
  });

  it("explains a read timeout without claiming that the provider failed", () => {
    const message = formatSafeRunError({
      code: "unknown_remote_outcome",
      provider_summary: {
        provider_error_type: "ReadTimeout",
        request_phase: "read",
        timeout_seconds: 90,
        elapsed_seconds: 90.2,
        provider_response_received: false,
      },
    });

    expect(message).toContain("90");
    expect(message).toContain("未收到可验证的模型响应");
    expect(message).toContain("结果仍可能已生成");
    expect(message).toContain("不会自动重试");
  });
});
