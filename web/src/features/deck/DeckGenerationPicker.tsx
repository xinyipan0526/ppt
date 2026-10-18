import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { DeckGeneration } from "../../api/contracts";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";

export function DeckGenerationPicker({ projectId, generations, selectedId, onSelect, onRestored }: { projectId: string; generations: DeckGeneration[]; selectedId: string; onSelect: (id: string) => void; onRestored: (deck: DeckGeneration) => void }) {
  const cache = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const currentGeneration = Math.max(...generations.map((item) => item.generation));
  const selected = generations.find((item) => item.id === selectedId) ?? generations[0];
  const historical = selected.generation !== currentGeneration;
  const restore = useMutation({
    mutationFn: () => apiClient.request<DeckGeneration>(`/api/v1/projects/${projectId}/deck-generations/${selected.id}/restore`, { method: "POST", body: { expected_generation: currentGeneration } }),
    onSuccess: (deck) => { setConfirming(false); onRestored(deck); void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] }); },
  });
  return <div className="card panel version-picker">
    <label className="field"><span>演示文稿版本</span><select value={selected.id} onChange={(event) => onSelect(event.target.value)}>{generations.slice().sort((a, b) => b.generation - a.generation).map((item) => <option key={item.id} value={item.id}>版本 {item.generation}{item.generation === currentGeneration ? " · 当前版本" : " · 历史版本"}</option>)}</select></label>
    {historical && <button className="button" onClick={() => setConfirming(true)}>恢复为新版本</button>}
    {restore.error && <ErrorBanner error={restore.error} fallback="版本恢复失败；可能已有更新版本，请刷新后重试。" />}
    <ConfirmDialog open={confirming} title="恢复历史版本" confirmLabel="确认恢复" busy={restore.isPending} onClose={() => setConfirming(false)} onConfirm={() => restore.mutate()}><p>历史版本不会覆盖现有内容，系统会基于它创建版本 {currentGeneration + 1}。</p></ConfirmDialog>
  </div>;
}
