import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { DeckGeneration } from "../../api/contracts";

export function GlobalRegenerateDialog({ projectId, deck, onClose, onCreated }: { projectId: string; deck: DeckGeneration; onClose: () => void; onCreated: (runId: string) => void }) {
  const [instruction, setInstruction] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const regenerate = useMutation({ mutationFn: () => apiClient.request<{ run_id: string }>(`/api/v1/projects/${projectId}/deck-generations/${deck.id}/regenerate`, { method: "POST", body: { instruction, expected_generation: deck.generation } }), onSuccess: (value) => { onCreated(value.run_id); onClose(); } });
  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="全局重生成"><div className="modal card"><p className="eyebrow">Global regeneration</p><h2>重新生成整套 PPT</h2><p className="muted">将创建新的 Deck generation，现有版本和所有页面历史会保留。云模式下会重新产生图片费用。</p><div className="field"><label>全局调整 Prompt</label><textarea rows={5} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="例如：整体更克制、留白更多，配色改为深蓝与荧光绿。" /></div><label style={{ display: "block", marginTop: 14 }}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> 我确认重新生成全部页面并可能产生费用</label><div className="toolbar end" style={{ marginTop: 18 }}><button className="button" onClick={onClose}>取消</button><button className="button primary" disabled={!confirmed || !instruction.trim() || regenerate.isPending} onClick={() => regenerate.mutate()}>创建新版本</button></div></div></div>;
}
