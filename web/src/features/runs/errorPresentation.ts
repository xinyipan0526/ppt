import type { SafeRunError } from "../../api/contracts";

export function formatSafeRunError(error: SafeRunError): string {
  const provider = error.provider_summary;
  if (
    error.code === "unknown_remote_outcome"
    && provider?.request_phase === "read"
    && provider.provider_response_received === false
  ) {
    const timeout = provider.timeout_seconds !== undefined
      ? `${provider.timeout_seconds} 秒`
      : "设定时间内";
    return `读取模型响应超过${timeout}，未收到可验证的模型响应；远端结果仍可能已生成。为避免重复计费，系统不会自动重试，请在运行详情中核对并处理。`;
  }
  const details = [
    provider?.provider_error_type,
    provider?.http_status && `HTTP ${provider.http_status}`,
  ].filter(Boolean);
  const guidance: Record<string, string> = {
    content_rejected: "模型返回内容未通过结构校验，请重试；如持续失败，请查看日志和运行轨迹。",
    invalid_request: "模型请求参数不兼容，请检查模型配置或更新服务。",
    authentication_error: "模型认证失败，请重新保存 Ark API Key。",
    insufficient_balance: "模型账户余额不足，请检查火山方舟账户。",
    rate_limited: "模型服务限流，请稍后重试。",
    temporary_unavailable: "模型服务暂时不可用，请稍后重试。",
    unknown_remote_outcome: "远端结果未知，为避免重复计费，请按恢复提示处理。",
  };
  const summary = guidance[error.code] ?? `任务失败：${error.code}`;
  return details.length ? `${summary} (${details.join(", ")})` : summary;
}
