import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { SafeRunError, RunTrace as RunTraceData } from "../../api/contracts";

export function RunTrace({ runId }: { runId: string }) {
  const trace = useQuery({
    queryKey: ["run-trace", runId],
    queryFn: () => apiClient.request<RunTraceData>(`/api/v1/runs/${runId}/trace`),
    refetchInterval: 5_000,
  });
  if (trace.isLoading) return <div className="skeleton" />;
  if (!trace.data) return <div className="error-banner">Unable to load the safe run trace.</div>;
  const { run, steps, events, operations } = trace.data;
  return <section className="card panel" aria-label="Run trace">
    <div className="toolbar" style={{ justifyContent: "space-between" }}><div><p className="eyebrow">Run trace</p><strong>{run.id}</strong></div><span className={`badge ${run.status}`}>{run.status}</span></div>
    {run.error && <div className="error-banner" style={{ marginTop: 12 }}><strong>{run.status === "failed" ? "Run failed" : "Run error"}:</strong> {formatError(run.error)}</div>}
    <TraceSection title="Steps">{steps.map((step) => <li key={step.id} className="toolbar"><span className={`badge ${step.status}`}>{step.status}</span><span>{step.step_key}</span>{step.error && <span className="muted">{formatError(step.error)}</span>}</li>)}</TraceSection>
    <TraceSection title="Events">{events.map((event) => <li key={event.event_id} className="toolbar"><span className="muted">#{event.run_seq}</span><strong>{event.event_type}</strong><time className="muted">{event.occurred_at}</time></li>)}</TraceSection>
    {operations.length > 0 && <TraceSection title="Operations">{operations.map(({ operation, attempts }) => <li key={operation.id} className="grid"><div className="toolbar"><strong>{operation.operation_kind}</strong><span className={`badge ${operation.aggregate_status}`}>{operation.aggregate_status}</span><span className="muted">{operation.provider} / {operation.model}</span></div>{attempts.map((attempt) => <div key={attempt.id} className="toolbar"><span className={`badge ${attempt.status}`}>{attempt.status}</span><span>#{attempt.attempt_no}</span>{attempt.error_code && <span className="muted">{attempt.error_code}</span>}</div>)}</li>)}</TraceSection>}
  </section>;
}

function TraceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <div style={{ marginTop: 16 }}><h4 style={{ margin: "0 0 8px" }}>{title}</h4><ol className="grid" style={{ margin: 0, paddingLeft: 20 }}>{children}</ol></div>;
}

function formatError(error: SafeRunError): string {
  const details = [error.provider_summary?.provider_error_type, error.provider_summary?.http_status && `HTTP ${error.provider_summary.http_status}`].filter(Boolean);
  return details.length ? `${error.code} (${details.join(", ")})` : error.code;
}
