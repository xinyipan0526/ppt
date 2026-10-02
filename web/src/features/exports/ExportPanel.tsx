import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";

type ExportFormat = "pptx" | "pdf" | "png_zip" | "jpg_zip" | "png" | "jpg";

export function ExportPanel({ runId }: { runId: string }) {
  const create = useMutation({
    mutationFn: (format: ExportFormat) => apiClient.request<{ export_id: string }>(
      `/api/v1/runs/${runId}/exports`,
      { method: "POST", body: { format, forced: false } }
    ),
    onSuccess: (value) => {
      window.location.assign(`/api/v1/exports/${value.export_id}/download`);
    }
  });
  return <section className="card panel">
    <div className="toolbar" style={{ justifyContent: "space-between" }}>
      <div><p className="eyebrow">Deliver</p><h3 style={{ margin: 0 }}>导出成品</h3></div>
      <span className="badge">图片型 PPTX</span>
    </div>
    <p className="muted" style={{ fontSize: 13 }}>每页保持视觉一致，适合直接放映与分享。</p>
    <div className="toolbar">
      <button className="button primary" disabled={create.isPending} onClick={() => create.mutate("pptx")}>下载 PPTX</button>
      <button className="button" onClick={() => create.mutate("pdf")}>PDF</button>
      <button className="button" onClick={() => create.mutate("png_zip")}>PNG ZIP</button>
      <button className="button" onClick={() => create.mutate("jpg_zip")}>JPG ZIP</button>
    </div>
    {create.error && <p className="error-banner">导出失败。若页面质检未通过，请先修复或明确确认风险。</p>}
  </section>;
}
