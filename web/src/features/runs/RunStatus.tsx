import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { CancelRunResponse, RunSnapshot } from "../../api/contracts";
import { RunEventStream } from "../../api/events";
import { RecoveryActions } from "./RecoveryActions";
import { RunTrace } from "./RunTrace";
import type { SafeRunError } from "../../api/contracts";
import { formatSafeRunError } from "./errorPresentation";
import { ErrorBanner } from "../../components/ErrorBanner";
import { ProgressBar } from "../../components/ProgressBar";
import { Icon } from "../../components/Icon";
import { getStatusPresentation } from "./statusPresentation";

export function RunStatus({ runId, onChange, onDismiss, paidActionsDisabled = false }: { runId: string; onChange: () => void; onDismiss: () => void; paidActionsDisabled?: boolean }) {
  const cache = useQueryClient();
  const [dismissedNoticeKey, setDismissedNoticeKey] = useState<string | null>(null);
  const [terminalGeneration, setTerminalGeneration] = useState(0);
  const [confirmingCancellation, setConfirmingCancellation] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [unresolvedRemoteCount, setUnresolvedRemoteCount] = useState<number | null>(null);
  const previousStatus = useRef<{ runId: string; status: RunSnapshot["run"]["status"] } | null>(null);
  const cancellationInFlight = useRef(false);
  const snapshot = useQuery({ queryKey: ["run-snapshot", runId], queryFn: () => apiClient.request<RunSnapshot>(`/api/v1/runs/${runId}/snapshot`), refetchInterval: 5_000 });
  const cancellation = useMutation({
    mutationFn: () => apiClient.request<CancelRunResponse>(`/api/v1/runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: () => { setConfirmingCancellation(false); void snapshot.refetch(); onChange(); },
    onSettled: () => { cancellationInFlight.current = false; },
  });
  useEffect(() => { const stream = new RunEventStream(runId); const refresh = () => { void snapshot.refetch(); if (detailsOpen) void cache.invalidateQueries({ queryKey: ["run-trace", runId] }); onChange(); }; return stream.connect(refresh, refresh); }, [runId, detailsOpen]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setDismissedNoticeKey(null); setTerminalGeneration(0); setConfirmingCancellation(false); setDetailsOpen(false); setUnresolvedRemoteCount(null); cancellationInFlight.current = false; previousStatus.current = null; }, [runId]);
  const status = snapshot.data?.run.status;
  const exceptional = status ? isExceptional(status) : false;
  useEffect(() => {
    if (!status) return;
    if (previousStatus.current?.runId === runId && !isExceptional(previousStatus.current.status) && exceptional) setTerminalGeneration((generation) => generation + 1);
    previousStatus.current = { runId, status };
  }, [runId, status, exceptional]);
  if (snapshot.isLoading) return <div className="card panel run-loading" role="status" aria-live="polite"><span className="loading-spinner" aria-hidden="true" /><span>正在加载任务…</span></div>;
  if (snapshot.isError) return <section className="card panel error-banner" role="alert" aria-live="assertive"><strong>无法读取任务状态：</strong>{snapshot.error instanceof Error ? snapshot.error.message : "请检查本地服务后重试。"}<div className="toolbar run-notice-actions"><button className="button" onClick={() => { void snapshot.refetch(); }}>重试</button></div></section>;
  if (!snapshot.data) return null;
  const currentStatus = snapshot.data.run.status;
  const error = snapshot.data.run.error ?? null;
  const progress = pageProgress(snapshot.data.pages);
  const currentStep = snapshot.data.steps.find((step) => isActiveStep(step.status));
  const presentation = getStatusPresentation(currentStatus);
  const fingerprint = error ? `${error.code}:${error.provider_summary?.provider_error_type ?? ""}:${error.provider_summary?.http_status ?? ""}` : "none";
  const noticeKey = `${terminalGeneration}:${currentStatus}:${fingerprint}`;
  const progressLabel = `页面进度：${progress.completed} / ${progress.total} 已完成，${progress.failed} 失败`;
  return <>
    <section className="card panel">
      <div className="toolbar spread">
        <div><p className="eyebrow">Run</p><h3 className="section-title">任务进度</h3></div>
        <span className={`badge ${presentation.tone}`}><Icon name={presentation.icon} />{presentation.label}<span className="sr-only">{currentStatus}</span></span>
      </div>
      <ProgressBar value={progress.total ? progress.completed : undefined} max={progress.total || undefined} label={progressLabel} />
      {presentation.guidance && <p className="muted">{presentation.guidance}{currentStatus === "waiting_for_retry" && snapshot.data.run.retry_at ? ` 下次尝试：${new Date(snapshot.data.run.retry_at).toLocaleString("zh-CN")}` : ""}</p>}
      <div className="grid run-step">{currentStep ? <div className="toolbar"><span className={`badge ${currentStep.status}`}>{getStatusPresentation(currentStep.status).label}</span><span>{String(currentStep.values.step_key ?? "处理步骤")}</span></div> : <p className="muted">当前没有执行中的步骤。</p>}</div>
      {exceptional && dismissedNoticeKey !== noticeKey && <RunFailureNotice status={currentStatus} error={error} onOpenDetails={() => setDetailsOpen(true)} onClose={() => setDismissedNoticeKey(noticeKey)} />}
      {currentStatus === "cancel_requested" && <div className="toolbar end run-actions"><span className="muted">取消正在等待安全收尾。</span><button className="button" disabled={cancellation.isPending} onClick={() => { if (!cancellationInFlight.current) { cancellationInFlight.current = true; cancellation.mutate(); } }}>{cancellation.isPending ? "正在完成取消…" : "继续完成取消"}</button>{cancellation.error && <ErrorBanner error={cancellation.error} fallback="取消收尾失败，请重试。" />}</div>}
      {isCancellable(currentStatus) && <div className="toolbar end run-actions">{confirmingCancellation ? <><span className="muted">确定要取消任务吗？</span><button className="button" disabled={cancellation.isPending} onClick={() => { if (!cancellationInFlight.current) { cancellationInFlight.current = true; cancellation.mutate(); } }}>{cancellation.isPending ? "正在取消…" : "确认取消任务"}</button><button className="button" disabled={cancellation.isPending} onClick={() => setConfirmingCancellation(false)}>继续生成</button></> : <button className="button" onClick={() => setConfirmingCancellation(true)}>取消任务</button>}{cancellation.error && <ErrorBanner error={cancellation.error} fallback="取消请求失败，请重试。" />}</div>}
      {["failed", "cancelled", "succeeded"].includes(currentStatus) && <div className="toolbar end run-actions"><button className="button" onClick={onDismiss}>关闭任务</button></div>}
      <div className="run-details-toggle"><button className="button" type="button" aria-expanded={detailsOpen} aria-controls="run-details" onClick={() => setDetailsOpen((open) => !open)}>{detailsOpen ? "收起运行详情" : "运行详情"}</button></div>
    </section>
    {detailsOpen && <div id="run-details" className="run-details" role="region" aria-label="运行详情">
      {currentStatus === "recovery_required" && unresolvedRemoteCount !== null && <div className="run-actions"><RecoveryActions runId={runId} unresolvedRemoteCount={unresolvedRemoteCount} disabled={paidActionsDisabled} onRecovered={() => { void snapshot.refetch(); }} /></div>}
      <RunTrace runId={runId} onRecoveryChange={setUnresolvedRemoteCount} />
    </div>}
  </>;
}

function RunFailureNotice({ status, error, onOpenDetails, onClose }: { status: RunSnapshot["run"]["status"]; error: SafeRunError | null; onOpenDetails: () => void; onClose: () => void }) {
  const heading = status === "recovery_required" ? "任务需要人工恢复" : status === "cancelled" ? "任务已取消" : "任务失败";
  const message = error ? formatSafeRunError(error) : "系统未返回可展示的错误详情，请查看下方轨迹和本地日志。";
  return <div className="error-banner run-notice" role="alert" aria-live="assertive">
    <strong>{heading}：</strong>{message}
    <div className="toolbar run-notice-actions">
      <button className="button" type="button" onClick={onOpenDetails}>打开运行详情</button>
      <button className="button" onClick={onClose}>关闭提示</button>
    </div>
  </div>;
}

function isExceptional(status: RunSnapshot["run"]["status"]): boolean {
  return ["failed", "recovery_required", "cancelled"].includes(status);
}

function isCancellable(status: RunSnapshot["run"]["status"]): boolean {
  return ["queued", "running", "waiting_for_user", "waiting_for_retry", "recovery_required"].includes(status);
}

function isActiveStep(status: string): boolean {
  return ["running", "waiting_for_user", "waiting_for_retry", "recovery_required", "cancel_requested"].includes(status);
}

function pageProgress(pages: RunSnapshot["pages"]): { completed: number; total: number; failed: number } {
  return {
    completed: pages.filter((page) => page.status === "succeeded").length,
    total: pages.length,
    failed: pages.filter((page) => page.status === "failed").length,
  };
}
