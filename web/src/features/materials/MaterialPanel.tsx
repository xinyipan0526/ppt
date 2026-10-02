import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { MaterialItem } from "../../api/contracts";
import { UploadDropzone } from "./UploadDropzone";

export function MaterialPanel({ projectId, materials, selected, onSelected }: { projectId: string; materials: MaterialItem[]; selected: string[]; onSelected: (ids: string[]) => void }) {
  const cache = useQueryClient();
  const refresh = () => cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
  const upload = useMutation({ mutationFn: (file: File) => apiClient.request(`/api/v1/projects/${projectId}/materials?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file }), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (item: MaterialItem) => apiClient.request(`/api/v1/projects/${projectId}/materials/${item.id}/retry`, { method: "POST", body: { expected_version: item.version ?? 1 } }), onSuccess: refresh });
  return <section className="card panel"><div className="toolbar" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>资料</h3><span className="badge">{materials.length}</span></div><p className="muted" style={{ fontSize: 12 }}>仅“就绪”的资料会进入规划上下文。</p><UploadDropzone busy={upload.isPending} onUpload={(file) => upload.mutate(file)} />
    <div className="grid" style={{ marginTop: 14 }}>{materials.map((item) => <label key={item.id} className="toolbar" style={{ alignItems: "flex-start" }}><input type="checkbox" checked={selected.includes(item.id)} disabled={item.status !== "ready"} onChange={(event) => onSelected(event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id))} /><span style={{ minWidth: 0, flex: 1 }}><strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>{item.filename ?? item.display_name}</strong><span className={`badge ${item.status}`} style={{ marginTop: 5 }}>{item.status}</span>{item.status === "failed" && <button type="button" className="button" style={{ margin: "6px 0 0 8px", padding: "4px 7px" }} onClick={(e) => { e.preventDefault(); retry.mutate(item); }}>重试</button>}</span></label>)}</div>
    {upload.error && <p className="error-banner" style={{ marginTop: 12 }}>上传失败，请检查格式和大小。</p>}
  </section>;
}
