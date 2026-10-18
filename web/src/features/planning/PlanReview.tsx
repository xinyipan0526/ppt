import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PlanDetail } from "../../api/contracts";
import { PageSpecCard } from "./PageSpecCard";
import { ErrorBanner } from "../../components/ErrorBanner";

export function PlanReview({ projectId, runId, interrupt, onApproved, disabled = false }: { projectId: string; runId: string; interrupt: { id: string; values: Record<string, unknown> }; onApproved: () => void; disabled?: boolean }) {
  const cache = useQueryClient();
  const [feedback, setFeedback] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const plan = useQuery({ queryKey: ["plan-detail", runId], queryFn: () => apiClient.request<PlanDetail>(`/api/v1/runs/${runId}/plan-detail`) });
  const respond = useMutation({ mutationFn: (payload: Record<string, unknown>) => apiClient.request(`/api/v1/runs/${runId}/interrupts/${interrupt.id}/resume`, { method: "POST", body: { expected_version: interrupt.values.version, payload } }), onSuccess: () => { void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] }); onApproved(); } });
  const revise = () => respond.mutate({ action: "revise", feedback, scope: selected.length ? "pages" : "auto", page_indexes: selected });
  return <section>
    <div className="page-head"><div><p className="eyebrow">Plan review</p><h1>生成前，先确认完整规划</h1><p className="muted">确认目标、叙事、设计系统和逐页内容后才会产生图片费用。</p></div><div className="toolbar"><button className="button danger" onClick={() => respond.mutate({ action: "cancel" })}>取消</button><button className="button primary" disabled={disabled || plan.isError || !plan.data?.review_ready || respond.isPending} onClick={() => respond.mutate({ action: "approve" })}>批准规划</button></div></div>
    {plan.isError && <ErrorBanner error={plan.error} fallback="无法读取规划，请刷新后重试。" />}
    {respond.error && <ErrorBanner error={respond.error} fallback="规划复核提交失败，请稍后重试。" />}
    {plan.isLoading && <div className="skeleton" />}
    {plan.data && <>
      {plan.data.brief && <article className="card panel plan-section"><p className="eyebrow">Brief</p><h2>{plan.data.brief.topic}</h2><dl className="detail-list"><dt>用途</dt><dd>{plan.data.brief.purpose || "未指定"}</dd><dt>受众</dt><dd>{plan.data.brief.audience || "未指定"}</dd><dt>语气</dt><dd>{plan.data.brief.tone || "未指定"}</dd><dt>页数与质量</dt><dd>{plan.data.brief.page_count} 页 · {plan.data.brief.quality}</dd></dl></article>}
      {plan.data.outline && <article className="card panel plan-section"><p className="eyebrow">Outline</p><h2>叙事结构</h2><p>{plan.data.outline.narrative}</p><ol>{plan.data.outline.pages.map((item) => <li key={item.page_index}>{item.section ? `${item.section}：` : ""}{item.objective}</li>)}</ol></article>}
      {plan.data.design_system && <article className="card panel plan-section"><p className="eyebrow">Design system</p><h2>视觉系统</h2><dl className="detail-list"><dt>视觉风格</dt><dd>{plan.data.design_system.visual_style}</dd><dt>图片风格</dt><dd>{plan.data.design_system.image_style}</dd><dt>色板</dt><dd>{plan.data.design_system.color_palette.join(" · ")}</dd><dt>禁止项</dt><dd>{plan.data.design_system.prohibited.join("、") || "无"}</dd></dl></article>}
      <div className="plan-page-grid">{plan.data.pages.map((page) => <PageSpecCard key={page.page_index} page={page} selected={selected.includes(page.page_index)} disabled={disabled} onToggle={() => setSelected(selected.includes(page.page_index) ? selected.filter((number) => number !== page.page_index) : [...selected, page.page_index])} />)}</div>
      <div className="card panel plan-feedback"><div className="field"><label>修改意见（选择页面可定向修改）</label><textarea rows={3} disabled={disabled} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="例如：第 3 页更简洁，突出核心数字。" /></div><div className="action-row"><button className="button" disabled={disabled || !feedback.trim() || respond.isPending} onClick={revise}>提交修改</button></div></div>
    </>}
  </section>;
}
