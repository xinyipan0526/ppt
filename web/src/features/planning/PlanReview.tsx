import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PlanDetail } from "../../api/contracts";
import { PageSpecCard } from "./PageSpecCard";

export function PlanReview({ projectId, runId, interrupt, onApproved }: { projectId: string; runId: string; interrupt: { id: string; values: Record<string, unknown> }; onApproved: () => void }) {
  const cache = useQueryClient(); const [feedback, setFeedback] = useState(""); const [selected, setSelected] = useState<number[]>([]);
  const plan = useQuery({ queryKey: ["plan-detail", runId], queryFn: () => apiClient.request<PlanDetail>(`/api/v1/runs/${runId}/plan-detail`) });
  const respond = useMutation({ mutationFn: (payload: Record<string, unknown>) => apiClient.request(`/api/v1/runs/${runId}/interrupts/${interrupt.id}/resume`, { method: "POST", body: { expected_version: interrupt.values.version, payload } }), onSuccess: () => { void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] }); onApproved(); } });
  const revise = () => respond.mutate({ action: "revise", feedback, scope: selected.length ? "pages" : "auto", page_indexes: selected });
  return <section><div className="page-head"><div><p className="eyebrow">Plan review</p><h1>生成前，先确认每一页</h1><p className="muted">内容结构和视觉方向仍可修改，不会产生图片费用。</p></div><div className="toolbar"><button className="button danger" onClick={() => respond.mutate({ action: "cancel" })}>取消</button><button className="button primary" disabled={!plan.data?.review_ready || respond.isPending} onClick={() => respond.mutate({ action: "approve" })}>批准规划</button></div></div>
    {plan.isLoading && <div className="skeleton" />}{plan.data && <><div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))" }}>{plan.data.pages.map((page, i) => <PageSpecCard key={i} page={page} selected={selected.includes(i + 1)} onToggle={() => setSelected(selected.includes(i + 1) ? selected.filter((n) => n !== i + 1) : [...selected, i + 1])} />)}</div><div className="card panel" style={{ marginTop: 18 }}><div className="field"><label>修改意见（选择页面可定向修改）</label><textarea rows={3} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="例如：第 3 页更简洁，突出核心数字。" /></div><div className="toolbar end" style={{ marginTop: 10 }}><button className="button" disabled={!feedback.trim()} onClick={revise}>提交修改</button></div></div></>}
  </section>;
}
