import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PageDetail } from "../../api/contracts";
import { PageHistory } from "./PageHistory";
import { QualitySummary } from "./QualitySummary";
import { Dialog } from "../../components/Dialog";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";

type InspectorTab = "content" | "design" | "quality" | "edit" | "history";

export function PageInspector({ page, onClose, disabled = false }: { page: PageDetail; onClose: () => void; disabled?: boolean }) {
  const [tab, setTab] = useState<InspectorTab>(page.pending_edit ? "edit" : "content");
  const [instruction, setInstruction] = useState("");
  const [confirmingVisual, setConfirmingVisual] = useState(false);
  const [confirmingRegeneration, setConfirmingRegeneration] = useState(false);
  const cache = useQueryClient();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const refresh = () => { void cache.invalidateQueries({ queryKey: ["page", page.id] }); void cache.invalidateQueries({ queryKey: ["page-history", page.id] }); };
  const edit = useMutation({
    mutationFn: (resampleVisual: boolean) => apiClient.request(`/api/v1/pages/${page.id}/edits`, { method: "POST", body: { instruction, idempotency_key: crypto.randomUUID(), resample_visual: resampleVisual } }),
    onSuccess: () => { setInstruction(""); setConfirmingVisual(false); refresh(); },
  });
  const resume = useMutation({
    mutationFn: () => apiClient.request(`/api/v1/page-edits/${page.pending_edit!.id}/resume`, { method: "POST", body: { instruction, target_box: page.pending_edit!.target_box } }),
    onSuccess: () => { setInstruction(""); refresh(); },
  });
  const regenerate = useMutation({
    mutationFn: () => apiClient.request(`/api/v1/pages/${page.id}/regenerate`, { method: "POST", body: { instruction: "重新生成本页", idempotency_key: crypto.randomUUID(), resample_visual: true } }),
    onSuccess: () => { setConfirmingRegeneration(false); refresh(); },
  });
  const busy = edit.isPending || resume.isPending || regenerate.isPending;
  const failure = edit.error ?? resume.error ?? regenerate.error;
  const labels: Record<InspectorTab, string> = { content: "交付文字", design: "设计 Prompt", quality: "质量检查", edit: "局部修改", history: "历史版本" };
  return <>
    <Dialog title={`第 ${page.page_index} 页：页面设计与修改`} open onClose={onClose} busy={busy} initialFocusRef={closeButtonRef}>
      <div className="toolbar spread"><p className="eyebrow">Slide {page.page_index}</p><div className="toolbar"><button className="button danger" disabled={disabled || busy} onClick={() => setConfirmingRegeneration(true)}>{regenerate.isPending ? "生成中…" : "重生成本页"}</button><button ref={closeButtonRef} className="button" disabled={busy} onClick={onClose}>关闭</button></div></div>
      {page.current_generation.page_image_url && <div className="slide-frame inspector-preview"><img src={page.current_generation.page_image_url} alt={`第 ${page.page_index} 页`} /></div>}
      <div className="tabs">{(Object.keys(labels) as InspectorTab[]).map((name) => <button key={name} className={tab === name ? "active" : ""} disabled={busy} onClick={() => setTab(name)}>{labels[name]}</button>)}</div>
      {tab === "content" && <div className="grid">{page.current_generation.delivery_text.map((entry) => <div className="field" key={entry.id}><label>{entry.role}</label><textarea readOnly rows={entry.role === "title" ? 2 : 4} value={entry.text} /></div>)}</div>}
      {tab === "design" && <div className="field"><label>当前视觉生成指令</label><textarea readOnly rows={8} value={page.current_generation.design_prompt || "本页采用确定性排版，无生成式视觉 Prompt。"} /></div>}
      {tab === "quality" && <QualitySummary quality={page.current_generation.quality} />}
      {tab === "edit" && (page.pending_edit ? <div className="card panel clarification-card"><p className="eyebrow">待补充信息</p><h3>{page.pending_edit.clarification_question || "请补充本次修改的定位信息"}</h3>{page.pending_edit.target_box && <p className="muted">已保存服务端识别的目标区域，可直接补充说明后继续。</p>}<label className="field"><span>补充说明</span><textarea rows={4} disabled={disabled || busy} value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label><div className="action-row"><button className="button primary" disabled={disabled || busy || !instruction.trim()} onClick={() => resume.mutate()}>继续修改</button></div></div> : <><div className="field"><label>局部修改 Prompt</label><textarea rows={5} disabled={disabled || busy} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：把标题改成……；将右侧图片改为更冷静的蓝色科技风。" /></div>{page.current_generation.local_edit_prompt && <p className="muted">上次修改：{page.current_generation.local_edit_prompt}</p>}<div className="action-row"><button className="button" disabled={disabled || !instruction.trim() || busy} onClick={() => edit.mutate(false)}>执行修改</button><button className="button primary" disabled={disabled || !instruction.trim() || busy} onClick={() => setConfirmingVisual(true)}>修改视觉图片（可能产生费用）</button></div></>)}
      {tab === "history" && <PageHistory pageId={page.id} currentId={page.current_generation.id} />}
      {failure && <ErrorBanner error={failure} fallback="页面操作失败；若远端结果未知，请返回任务状态按恢复提示处理。" />}
    </Dialog>
    <ConfirmDialog open={confirmingVisual} title="确认视觉修改" confirmLabel="确认视觉修改" acknowledgement="我了解视觉修改可能调用图片模型并产生费用" busy={edit.isPending} onClose={() => setConfirmingVisual(false)} onConfirm={() => edit.mutate(true)}><p>系统会重新生成或编辑本页视觉图片，交付文字仍由确定性排版合成。</p></ConfirmDialog>
    <ConfirmDialog open={confirmingRegeneration} title="确认重生成本页" confirmLabel="确认重生成" acknowledgement="我了解本次操作可能调用图片模型并产生费用" danger busy={regenerate.isPending} onClose={() => setConfirmingRegeneration(false)} onConfirm={() => regenerate.mutate()}><p>将创建新的页面代际；当前版本仍会保留在历史记录中。</p></ConfirmDialog>
  </>;
}
