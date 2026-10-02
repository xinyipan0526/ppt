import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";

export function RecoveryActions({ runId, onRecovered }: { runId: string; onRecovered: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const retry = useMutation({ mutationFn: () => apiClient.request(`/api/v1/runs/${runId}/authorize-local-retry`, { method: "POST", body: { reason: "用户在产品界面确认本地步骤可安全重试" } }), onSuccess: onRecovered });
  return <div className="error-banner"><strong>任务需要人工恢复</strong><p>系统不会自动重发结果未知的云请求，避免重复计费。请先确认 Provider 控制台没有成功结果。</p><label><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> 我已检查，本地步骤可安全重试</label><div style={{ marginTop: 10 }}><button className="button" disabled={!confirmed || retry.isPending} onClick={() => retry.mutate()}>授权恢复</button></div></div>;
}
