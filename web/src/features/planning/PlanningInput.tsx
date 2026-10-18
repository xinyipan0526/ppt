import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiClient } from "../../api/client";

export function PlanningInput({ projectId, materialIds, onCreated, disabled = false }: { projectId: string; materialIds: string[]; onCreated: (runId: string) => void; disabled?: boolean }) {
  const [text, setText] = useState("");
  const cache = useQueryClient();
  const create = useMutation({ mutationFn: () => apiClient.request<{ run_id: string }>(`/api/v1/projects/${projectId}/planning-runs`, { method: "POST", body: { normalized_text: text, material_ids: materialIds } }), onSuccess: (value) => { onCreated(value.run_id); void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] }); } });
  const errorMessage = create.error instanceof ApiError ? create.error.message : "无法创建规划，请检查输入或稍后重试。";
  return <section className="card panel"><h3>创作意图</h3><div className="field"><label htmlFor="planning-intent">这份 PPT 要解决什么问题？</label><textarea id="planning-intent" rows={5} disabled={disabled} value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：为管理层制作一份 8 页的季度复盘，突出增长来源、风险和下一步行动。" /></div><div className="toolbar end planning-actions"><span className="muted compact-copy">{materialIds.length} 份资料已选</span><button className="button primary" disabled={disabled || create.isPending || (!text.trim() && materialIds.length === 0)} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "开始规划"}</button></div>{create.error && <p className="error-banner" role="alert" aria-live="assertive">{errorMessage}</p>}</section>;
}
