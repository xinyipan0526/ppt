import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { DeckGeneration } from "../../api/contracts";
import { Dialog } from "../../components/Dialog";
import { ErrorBanner } from "../../components/ErrorBanner";

export function GlobalRegenerateDialog({ projectId, deck, onClose, onCreated, disabled = false }: { projectId: string; deck: DeckGeneration; onClose: () => void; onCreated: (runId: string) => void; disabled?: boolean }) {
  const [instruction, setInstruction] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const instructionRef = useRef<HTMLTextAreaElement>(null);
  const regenerate = useMutation({ mutationFn: () => apiClient.request<{ run_id: string }>(`/api/v1/projects/${projectId}/deck-generations/${deck.id}/regenerate`, { method: "POST", body: { instruction, expected_generation: deck.generation } }), onSuccess: (value) => { onCreated(value.run_id); onClose(); } });
  return <Dialog title="重新生成整套 PPT" open onClose={onClose} busy={regenerate.isPending} initialFocusRef={instructionRef}><p className="eyebrow">Global regeneration</p><p className="muted">将创建新的 Deck generation，现有版本和所有页面历史会保留。云模式下会重新产生图片费用。</p><div className="field"><label>全局调整 Prompt</label><textarea ref={instructionRef} rows={5} disabled={disabled || regenerate.isPending} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="例如：整体更克制、留白更多，配色改为深蓝与荧光绿。" /></div><label className="confirmation-choice"><input type="checkbox" disabled={disabled || regenerate.isPending} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> 我确认重新生成全部页面并可能产生费用</label><div className="toolbar end dialog-actions"><button className="button" disabled={regenerate.isPending} onClick={onClose}>取消</button><button className="button primary" disabled={disabled || !confirmed || !instruction.trim() || regenerate.isPending} onClick={() => regenerate.mutate()}>创建新版本</button></div>{regenerate.error && <ErrorBanner error={regenerate.error} fallback="重新生成失败，请稍后重试。" />}</Dialog>;
}
