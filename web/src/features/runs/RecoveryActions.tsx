import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";

export function RecoveryActions({ runId, onRecovered, disabled = false, unresolvedRemoteCount = 0 }: { runId: string; onRecovered: () => void; disabled?: boolean; unresolvedRemoteCount?: number }) {
  const [confirmed, setConfirmed] = useState(false);
  const retry = useMutation({ mutationFn: () => apiClient.request(`/api/v1/runs/${runId}/authorize-local-retry`, { method: "POST", body: { reason: "用户在产品界面确认本地步骤可安全重试" } }), onSuccess: onRecovered });
  const remoteBlocked = unresolvedRemoteCount > 0;
  return <div className="error-banner"><strong>任务需要人工恢复</strong><p>系统不会自动重发结果未知的云请求，避免重复计费。请先确认 Provider 控制台没有成功结果。</p>{remoteBlocked && <p>还有 {unresolvedRemoteCount} 个远端操作需要先处理</p>}<label><input type="checkbox" disabled={disabled || remoteBlocked || retry.isPending} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> 我已检查，本地步骤可安全重试</label><div className="action-row"><button className="button" disabled={disabled || remoteBlocked || !confirmed || retry.isPending} onClick={() => retry.mutate()}>授权恢复</button></div>{retry.error && <ErrorBanner error={retry.error} fallback="恢复授权失败，请稍后重试。" />}</div>;
}
