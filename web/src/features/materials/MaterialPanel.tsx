import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { MaterialItem } from "../../api/contracts";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { UploadDropzone } from "./UploadDropzone";

export function MaterialPanel({ projectId, materials, selected, onSelected }: { projectId: string; materials: MaterialItem[]; selected: string[]; onSelected: (ids: string[]) => void }) {
  const cache = useQueryClient();
  const [deleting, setDeleting] = useState<MaterialItem | null>(null);
  const refresh = () => cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
  const upload = useMutation({ mutationFn: (file: File) => apiClient.request(`/api/v1/projects/${projectId}/materials?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file }), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (item: MaterialItem) => apiClient.request(`/api/v1/projects/${projectId}/materials/${item.id}/retry`, { method: "POST", body: { expected_version: item.version ?? 1 } }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (item: MaterialItem) => apiClient.request(`/api/v1/projects/${projectId}/materials/${item.id}`, { method: "DELETE" }), onSuccess: (_value, item) => { onSelected(selected.filter((id) => id !== item.id)); setDeleting(null); void refresh(); } });
  return <section className="card panel"><div className="toolbar spread"><h3>资料</h3><span className="badge">{materials.length}</span></div><p className="muted compact-copy">仅“就绪”的资料会进入规划上下文。</p><UploadDropzone busy={upload.isPending} onUpload={(file) => upload.mutate(file)} />
    <div className="material-list">{materials.map((item) => <div key={item.id} className="material-row"><label className="material-select"><input type="checkbox" checked={selected.includes(item.id)} disabled={item.status !== "ready"} onChange={(event) => onSelected(event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id))} /><span><strong>{item.filename ?? item.display_name}</strong><span className={`badge ${item.status}`}>{item.status}</span></span></label><div className="toolbar">{item.status === "failed" && <button type="button" className="button" onClick={() => retry.mutate(item)}><Icon name="refresh" />重试</button>}<button type="button" className="button danger" aria-label={`删除素材 ${item.filename ?? item.display_name}`} onClick={() => setDeleting(item)}><Icon name="trash" />删除</button></div></div>)}</div>
    {(upload.error || retry.error) && <ErrorBanner error={upload.error ?? retry.error} fallback="资料处理失败，请检查文件格式和大小后重试。" />}
    <ConfirmDialog open={Boolean(deleting)} title="删除素材" confirmLabel="确认删除素材" danger busy={remove.isPending} onClose={() => setDeleting(null)} onConfirm={() => { if (deleting) remove.mutate(deleting); }}><p>删除“{deleting?.filename ?? deleting?.display_name}”后，它将不再用于后续规划。</p>{remove.error && <ErrorBanner error={remove.error} fallback="素材删除失败，请重试。" />}</ConfirmDialog>
  </section>;
}
