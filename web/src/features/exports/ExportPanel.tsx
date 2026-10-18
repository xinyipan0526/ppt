import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, apiClient } from "../../api/client";
import type { ExportFormat, ExportSnapshot } from "../../api/contracts";
import { ErrorBanner } from "../../components/ErrorBanner";
import { ConfirmDialog } from "../../components/ConfirmDialog";

type ExportRequest = {
  format: ExportFormat;
  forced: boolean;
  risk_acknowledgement?: string;
  jpeg_quality?: number;
  pdf_dpi?: number;
};

type ActiveExport = { runId: string; id: string; storageKey: string };
type CreateExport = { runId: string; intent: number; request: ExportRequest; storageKey: string };

const STORAGE_PREFIX = "ppt-agent:active-export:";
const ACTIVE_STATUSES = new Set(["queued", "running"]);

function runStoragePrefix(runId: string) {
  return `${STORAGE_PREFIX}${encodeURIComponent(runId)}:`;
}

function storageKey(runId: string, request: ExportRequest) {
  const option = request.jpeg_quality ?? request.pdf_dpi ?? "default";
  return `${runStoragePrefix(runId)}${request.format}:${option}`;
}

function recoverActiveExport(runId: string): ActiveExport | null {
  const prefix = runStoragePrefix(runId);
  for (let index = 0; index < sessionStorage.length; index += 1) {
    const key = sessionStorage.key(index);
    if (key?.startsWith(prefix)) {
      const id = sessionStorage.getItem(key);
      if (id) return { runId, id, storageKey: key };
    }
  }
  return null;
}

function persistActiveExport(runId: string, active: ActiveExport) {
  const prefix = runStoragePrefix(runId);
  const oldKeys: string[] = [];
  for (let index = 0; index < sessionStorage.length; index += 1) {
    const key = sessionStorage.key(index);
    if (key?.startsWith(prefix) && key !== active.storageKey) oldKeys.push(key);
  }
  oldKeys.forEach((key) => sessionStorage.removeItem(key));
  sessionStorage.setItem(active.storageKey, active.id);
}

function exportLabel(format: ExportFormat) {
  return format.replace("_", " ").toUpperCase();
}

function safeTerminalError(snapshot: ExportSnapshot): ApiError {
  const fallback = snapshot.status === "cancelled" ? "导出已取消" : "导出失败";
  const code = snapshot.error?.code;
  const detail = snapshot.error?.detail?.trim();
  return new ApiError(detail || (code ? `${fallback}（${code}）` : fallback), 409, code);
}

export function ExportPanel({ runId }: { runId: string }) {
  return <ExportPanelForRun key={runId} runId={runId} />;
}

function ExportPanelForRun({ runId }: { runId: string }) {
  const cache = useQueryClient();
  const isCurrent = useRef(true);
  const currentIntent = useRef(0);
  const [jpegQuality, setJpegQuality] = useState(92);
  const [pdfDpi, setPdfDpi] = useState(144);
  const [active, setActive] = useState<ActiveExport | null>(() => recoverActiveExport(runId));
  const [createdSnapshot, setCreatedSnapshot] = useState<ExportSnapshot | null>(null);
  const [recoveryError, setRecoveryError] = useState<ApiError | null>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [forceRequest, setForceRequest] = useState<{ request: ExportRequest; storageKey: string } | null>(null);
  const [forceDialogOpen, setForceDialogOpen] = useState(false);
  const create = useMutation({
    mutationFn: (value: CreateExport) => apiClient.request<ExportSnapshot>(
      `/api/v1/runs/${value.runId}/exports`,
      { method: "POST", body: value.request }
    ),
    onSuccess: (snapshot, value) => {
      if (
        !isCurrent.current
        || value.intent !== currentIntent.current
      ) return;
      const next = { runId: value.runId, id: snapshot.id, storageKey: value.storageKey };
      persistActiveExport(value.runId, next);
      // A terminal export retry deliberately reuses its durable ID. Replace
      // the cached failed/cancelled snapshot immediately so polling resumes
      // from the queued response returned by the POST.
      cache.setQueryData(["export", value.runId, snapshot.id], snapshot);
      setCreateError(null);
      setForceRequest(null);
      setForceDialogOpen(false);
      setRecoveryError(null);
      setCreatedSnapshot(snapshot);
      setActive(next);
    },
    onError: (error, value) => {
      if (
        isCurrent.current
        && value.intent === currentIntent.current
      ) {
        setCreateError(error);
        if (error instanceof ApiError && error.status === 409 && (error.code === "export_not_ready" || error.message === "export_not_ready")) {
          setForceRequest({ request: value.request, storageKey: value.storageKey });
        }
      }
    },
  });
  const scopedActive = active?.runId === runId ? active : null;
  const status = useQuery({
    queryKey: ["export", runId, scopedActive?.id],
    queryFn: () => apiClient.request<ExportSnapshot>(
      `/api/v1/exports/${scopedActive!.id}`, { method: "GET" }
    ),
    enabled: Boolean(scopedActive?.id),
    refetchInterval: (query) => ACTIVE_STATUSES.has(query.state.data?.status ?? "") ? 2_000 : false,
  });

  useLayoutEffect(() => {
    isCurrent.current = true;
    return () => {
      isCurrent.current = false;
      currentIntent.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!(status.error instanceof ApiError) || status.error.status !== 404 || !scopedActive) return;
    sessionStorage.removeItem(scopedActive.storageKey);
    setActive((current) => current?.runId === runId && current.id === scopedActive.id ? null : current);
    setCreatedSnapshot(null);
    setRecoveryError(status.error);
  }, [runId, scopedActive, status.error]);

  const snapshot = status.data ?? (
    createdSnapshot?.run_id === runId && createdSnapshot.id === scopedActive?.id
      ? createdSnapshot
      : null
  );
  const activeJob = snapshot ? ACTIVE_STATUSES.has(snapshot.status) : Boolean(scopedActive && status.isPending);
  const creatingCurrentRun = create.isPending && create.variables?.runId === runId;

  const begin = (format: ExportFormat) => {
    const request: ExportRequest = { format, forced: false };
    if (format === "jpg" || format === "jpg_zip") request.jpeg_quality = jpegQuality;
    if (format === "pdf") request.pdf_dpi = pdfDpi;
    const key = storageKey(runId, request);
    if (activeJob || creatingCurrentRun) return;
    currentIntent.current += 1;
    setCreateError(null);
    setForceRequest(null);
    setForceDialogOpen(false);
    setRecoveryError(null);
    create.mutate({
      runId, intent: currentIntent.current, request, storageKey: key,
    });
  };

  return <section className="card panel">
    <div className="toolbar spread">
      <div><p className="eyebrow">Deliver</p><h3 className="section-title">导出成品</h3></div>
      <span className="badge">图片型 PPTX</span>
    </div>
    <p className="muted support-copy">导出任务在后台运行；完成后再由你选择下载。</p>
    <div className="toolbar">
      <label>JPG 质量 <input aria-label="JPG 质量" type="number" min={1} max={100} value={jpegQuality} disabled={activeJob} onChange={(event) => setJpegQuality(Math.min(100, Math.max(1, Number(event.target.value))))} /></label>
      <label>PDF DPI <input aria-label="PDF DPI" type="number" min={72} max={600} value={pdfDpi} disabled={activeJob} onChange={(event) => setPdfDpi(Math.min(600, Math.max(72, Number(event.target.value))))} /></label>
    </div>
    <div className="toolbar">
      <button className="button primary" disabled={creatingCurrentRun || activeJob} onClick={() => begin("pptx")}>生成 PPTX</button>
      <button className="button" disabled={creatingCurrentRun || activeJob} onClick={() => begin("pdf")}>生成 PDF</button>
      <button className="button" disabled={creatingCurrentRun || activeJob} onClick={() => begin("png_zip")}>生成 PNG ZIP</button>
      <button className="button" disabled={creatingCurrentRun || activeJob} onClick={() => begin("jpg_zip")}>生成 JPG ZIP</button>
    </div>
    {createError !== null && <ErrorBanner error={createError} fallback="导出失败。若页面质检未通过，请先修复或明确确认风险。" />}
    {forceRequest && <button className="button danger" onClick={() => setForceDialogOpen(true)}>仍要强制导出</button>}
    {status.error && !(status.error instanceof ApiError && status.error.status === 404) && <ErrorBanner error={status.error} fallback="无法读取导出状态，请稍后重试。" />}
    {recoveryError && <ErrorBanner error={recoveryError} fallback="导出记录已失效，请重新生成。" />}
    {snapshot?.status === "queued" && <p role="status" aria-live="polite">导出排队中…</p>}
    {snapshot?.status === "running" && <p role="status" aria-live="polite">正在导出…</p>}
    {snapshot && ["failed", "cancelled"].includes(snapshot.status) && <ErrorBanner error={safeTerminalError(snapshot)} fallback="导出未完成。" />}
    {snapshot?.status === "available" && <a className="button primary" href={`/api/v1/exports/${snapshot.id}/download`}>下载 {exportLabel(snapshot.format)}</a>}
    <ConfirmDialog open={forceDialogOpen && Boolean(forceRequest)} title="确认强制导出" confirmLabel="确认强制导出" acknowledgement="我已了解页面质量风险并仍要导出" danger busy={create.isPending} onClose={() => setForceDialogOpen(false)} onConfirm={() => {
      if (!forceRequest) return;
      currentIntent.current += 1;
      create.mutate({ runId, intent: currentIntent.current, storageKey: forceRequest.storageKey, request: { ...forceRequest.request, forced: true, risk_acknowledgement: "用户已在前端确认页面质量风险" } });
    }}><p>部分页面未通过质量检查，导出结果可能包含溢出、低对比度或其他视觉问题。</p></ConfirmDialog>
  </section>;
}
