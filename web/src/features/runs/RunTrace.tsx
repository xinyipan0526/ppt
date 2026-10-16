import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { RunTrace as RunTraceData } from "../../api/contracts";
import { formatSafeRunError } from "./errorPresentation";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";

type UnknownAttempt = { operationId: string; attemptId: string; label: string };

export function RunTrace({ runId, projectId, onRecoveryChange }: { runId: string; projectId?: string; onRecoveryChange?: (count: number) => void }) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState<UnknownAttempt | null>(null);
  const trace = useQuery({
    queryKey: ["run-trace", runId],
    queryFn: () => apiClient.request<RunTraceData>(`/api/v1/runs/${runId}/trace`),
    refetchInterval: 5_000,
  });
  const resolvedProjectId = projectId ?? trace.data?.run.project_id;
  const authorize = useMutation({
    mutationFn: (item: UnknownAttempt) => apiClient.request(`/api/v1/operations/${item.operationId}/authorize-retry`, { method: "POST", body: { expected_attempt_id: item.attemptId, reason: "用户已在 Provider 控制台核对结果并接受可能重复计费的风险" } }),
    onSuccess: () => {
      setSelected(null);
      void cache.invalidateQueries({ queryKey: ["run-trace", runId] });
      void cache.invalidateQueries({ queryKey: ["run-snapshot", runId] });
      if (resolvedProjectId) void cache.invalidateQueries({ queryKey: ["project-costs", resolvedProjectId] });
    },
  });
  const unresolved = trace.data?.operations.reduce((count, item) => count + item.attempts.filter((attempt) => !attempt.retry_authorized && ["outcome_unknown", "reconciling", "cancel_requested"].includes(attempt.status)).length, 0) ?? 0;
  useEffect(() => {
    if (trace.data) onRecoveryChange?.(unresolved);
  }, [onRecoveryChange, trace.data, unresolved]);
  if (trace.isLoading) return <div id="run-trace" className="skeleton" />;
  if (!trace.data) return <div id="run-trace" className="error-banner" role="alert" aria-live="polite">无法加载运行轨迹，请稍后重试。</div>;
  const { run, steps, events, operations } = trace.data;
  return <section id="run-trace" className="card panel" aria-label="Run trace">
    <div className="toolbar spread"><div><p className="eyebrow">Run trace</p><strong>{run.id}</strong></div><span className={`badge ${run.status}`}>{run.status}</span></div>
    {run.error && <div className="error-banner card-copy"><strong>{run.status === "failed" ? "Run failed" : "Run error"}:</strong> {formatSafeRunError(run.error)}</div>}
    <TraceSection title="Steps">{steps.map((step) => <li key={step.id} className="toolbar"><span className={`badge ${step.status}`}>{step.status}</span><span>{step.step_key}</span>{step.error && <span className="muted">{formatSafeRunError(step.error)}</span>}</li>)}</TraceSection>
    <TraceSection title="Events">{events.map((event) => <li key={event.event_id} className="toolbar"><span className="muted">#{event.run_seq}</span><strong>{event.event_type}</strong><time className="muted">{event.occurred_at}</time></li>)}</TraceSection>
    {operations.length > 0 && <TraceSection title="Operations">{operations.map(({ operation, attempts }) => <li key={operation.id} className="grid"><div className="toolbar"><strong>{operation.operation_kind}</strong><span className={`badge ${operation.aggregate_status}`}>{operation.aggregate_status}</span><span className="muted">{operation.provider} / {operation.model}</span></div>{attempts.map((attempt) => <div key={attempt.id} className="toolbar"><span className={`badge ${attempt.status}`}>{attempt.status}</span><span>#{attempt.attempt_no}</span>{attempt.error_code && <span className="muted">{attempt.error_code}</span>}{attempt.retry_authorized ? <span className="badge success">已授权</span> : attempt.status === "outcome_unknown" && <button className="button" onClick={() => setSelected({ operationId: operation.id, attemptId: attempt.id, label: `${operation.provider} / ${operation.model} #${attempt.attempt_no}` })}>处理未知结果</button>}</div>)}</li>)}</TraceSection>}
    <ConfirmDialog open={selected !== null} title="确认远端结果未知的重试风险" confirmLabel="授权重试" acknowledgement="我已在 Provider 控制台核对结果" danger busy={authorize.isPending} onClose={() => setSelected(null)} onConfirm={() => { if (selected) authorize.mutate(selected); }}><p>{selected?.label}</p><p>原请求可能已成功并产生费用。授权后再次提交可能重复计费。</p>{authorize.error && <ErrorBanner error={authorize.error} fallback="未知结果授权失败，请刷新轨迹后重试。" />}</ConfirmDialog>
  </section>;
}

function TraceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="trace-section"><h4 className="trace-heading">{title}</h4><ol className="grid trace-list">{children}</ol></div>;
}
