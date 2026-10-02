import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PageDetail } from "../../api/contracts";
import { PageHistory } from "./PageHistory";

export function PageInspector({ page, onClose }: { page: PageDetail; onClose: () => void }) {
  const [tab, setTab] = useState<"content" | "design" | "edit" | "history">("content"); const [instruction, setInstruction] = useState(""); const cache = useQueryClient();
  const edit = useMutation({ mutationFn: () => apiClient.request(`/api/v1/pages/${page.id}/edits`, { method: "POST", body: { instruction, idempotency_key: crypto.randomUUID(), resample_visual: false } }), onSuccess: () => { setInstruction(""); void cache.invalidateQueries({ queryKey: ["page", page.id] }); } });
  const regenerate = useMutation({ mutationFn: () => apiClient.request(`/api/v1/pages/${page.id}/regenerate`, { method: "POST", body: { instruction: "重新生成本页", idempotency_key: crypto.randomUUID(), resample_visual: true } }), onSuccess: () => { void cache.invalidateQueries({ queryKey: ["page", page.id] }); void cache.invalidateQueries({ queryKey: ["page-history", page.id] }); } });
  const regeneratePage = () => { if (window.confirm("重新生成本页可能调用图片与视觉模型并产生费用。继续吗？")) regenerate.mutate(); };
  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={`第 ${page.page_index} 页详情`}><div className="modal card"><div className="toolbar" style={{ justifyContent: "space-between" }}><div><p className="eyebrow">Slide {page.page_index}</p><h2>页面设计与修改</h2></div><div className="toolbar"><button className="button danger" disabled={regenerate.isPending} onClick={regeneratePage}>{regenerate.isPending ? "生成中…" : "重生成本页"}</button><button className="button" onClick={onClose}>关闭</button></div></div>{page.current_generation.page_image_url && <div className="slide-frame" style={{ marginBottom: 18 }}><img src={page.current_generation.page_image_url} alt={`第 ${page.page_index} 页`} /></div>}<div className="tabs">{(["content", "design", "edit", "history"] as const).map((name) => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{({ content: "交付文字", design: "设计 Prompt", edit: "局部修改", history: "历史版本" })[name]}</button>)}</div>
    {tab === "content" && <div className="grid">{page.current_generation.delivery_text.map((entry) => <div className="field" key={entry.id}><label>{entry.role}</label><textarea readOnly rows={entry.role === "title" ? 2 : 4} value={entry.text} /></div>)}</div>}
    {tab === "design" && <div className="field"><label>当前视觉生成指令</label><textarea readOnly rows={8} value={page.current_generation.design_prompt || "本页采用确定性排版，无生成式视觉 Prompt。"} /></div>}
    {tab === "edit" && <><div className="field"><label>局部修改 Prompt</label><textarea rows={5} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="例如：把标题改成……；将右侧图片改为更冷静的蓝色科技风。" /></div>{page.current_generation.local_edit_prompt && <p className="muted">上次修改：{page.current_generation.local_edit_prompt}</p>}<div className="toolbar end"><button className="button primary" disabled={!instruction.trim() || edit.isPending} onClick={() => edit.mutate()}>执行修改</button></div></>}
    {tab === "history" && <PageHistory pageId={page.id} currentId={page.current_generation.id} />}
    {(edit.isError || regenerate.isError) && <p className="error-banner">页面修改失败；若远端结果未知，请返回任务状态按恢复提示处理。</p>}
  </div></div>;
}
