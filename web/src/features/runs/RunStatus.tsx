import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { RunSnapshot } from "../../api/contracts";
import { RunEventStream } from "../../api/events";
import { RecoveryActions } from "./RecoveryActions";
import { RunTrace } from "./RunTrace";

export function RunStatus({ runId, onChange, onDismiss }: { runId: string; onChange: () => void; onDismiss: () => void }) {
  const cache = useQueryClient();
  const snapshot = useQuery({ queryKey: ["run-snapshot", runId], queryFn: () => apiClient.request<RunSnapshot>(`/api/v1/runs/${runId}/snapshot`), refetchInterval: 5_000 });
  useEffect(() => { const stream = new RunEventStream(runId); const refresh = () => { void snapshot.refetch(); void cache.invalidateQueries({ queryKey: ["run-trace", runId] }); onChange(); }; return stream.connect(refresh, refresh); }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!snapshot.data) return <div className="card panel"><div className="skeleton" /></div>;
  const status = snapshot.data.run.status;
  return <><section className="card panel"><div className="toolbar" style={{ justifyContent: "space-between" }}><div><p className="eyebrow">Run</p><h3 style={{ margin: 0 }}>任务进度</h3></div><span className={`badge ${status}`}>{status}</span></div><div className="grid" style={{ marginTop: 16 }}>{snapshot.data.steps.map((step) => <div className="toolbar" key={step.id}><span className={`badge ${step.status}`}>{step.status}</span><span>{String(step.values.step_key ?? "处理步骤")}</span></div>)}</div>{status === "recovery_required" && <div style={{ marginTop: 14 }}><RecoveryActions runId={runId} onRecovered={() => { void snapshot.refetch(); }} /></div>}{["failed", "cancelled", "succeeded"].includes(status) && <div className="toolbar end" style={{ marginTop: 14 }}><button className="button" onClick={onDismiss}>Dismiss run</button></div>}</section><RunTrace runId={runId} /></>;
}
