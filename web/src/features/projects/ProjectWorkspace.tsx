import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { ProjectWorkspaceData, RunSnapshot } from "../../api/contracts";
import { MaterialPanel } from "../materials/MaterialPanel";
import { PlanningInput } from "../planning/PlanningInput";
import { ClarificationForm } from "../planning/ClarificationForm";
import { PlanReview } from "../planning/PlanReview";
import { DeckGallery } from "../deck/DeckGallery";
import { RunStatus } from "../runs/RunStatus";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export function ProjectWorkspace() {
  const projectId = useParams().projectId!;
  const cache = useQueryClient();
  const [selectedMaterials, setSelectedMaterials] = useState<string[]>([]);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const workspace = useQuery({
    queryKey: ["project-workspace", projectId],
    queryFn: () => apiClient.request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/workspace`),
    refetchInterval: activeRun ? 4_000 : false,
  });
  const inferredRun = activeRun
    ?? workspace.data?.runs.find((run) => !TERMINAL.has(run.status))?.id
    ?? null;
  const snapshot = useQuery({
    queryKey: ["run-snapshot", inferredRun],
    queryFn: () => apiClient.request<RunSnapshot>(`/api/v1/runs/${inferredRun}/snapshot`),
    enabled: Boolean(inferredRun),
    refetchInterval: inferredRun ? 3_000 : false,
  });
  const pendingInterrupt = snapshot.data?.interrupts.find((item) => item.status === "pending");
  const latestDeck = useMemo(
    () => workspace.data?.deck_generations.slice().sort((a, b) => b.generation - a.generation)[0],
    [workspace.data],
  );
  const approvedPlanning = workspace.data?.runs.find(
    (run) => run.workflow === "deck_planning" && run.status === "succeeded",
  );
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
    if (inferredRun) void cache.invalidateQueries({ queryKey: ["run-snapshot", inferredRun] });
  };

  useEffect(() => {
    if (activeRun && snapshot.data && TERMINAL.has(snapshot.data.run.status)) {
      setActiveRun(null);
      void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
    }
  }, [activeRun, cache, projectId, snapshot.data]);

  if (workspace.isLoading) return <div className="skeleton" />;
  if (!workspace.data) {
    return <div className="error-banner">项目不存在或暂时无法读取。<Link to="/">返回项目</Link></div>;
  }
  const isReview = pendingInterrupt
    && (pendingInterrupt.values.form_schema as Record<string, unknown> | undefined)?.kind === "plan_review";

  return <section>
    <div className="toolbar" style={{ marginBottom: 18 }}>
      <Link className="muted" to="/">项目</Link><span className="muted">/</span><strong>{workspace.data.project.name}</strong>
    </div>
    {isReview && inferredRun
      ? <PlanReview projectId={projectId} runId={inferredRun} interrupt={pendingInterrupt} onApproved={refresh} />
      : <div className="workspace-layout">
          <aside className="side-stack">
            <MaterialPanel projectId={projectId} materials={workspace.data.materials} selected={selectedMaterials} onSelected={setSelectedMaterials} />
            <PlanningInput projectId={projectId} materialIds={selectedMaterials} onCreated={setActiveRun} />
            {inferredRun && <RunStatus runId={inferredRun} onChange={refresh} />}
          </aside>
          <div>
            {pendingInterrupt && inferredRun
              ? <ClarificationForm runId={inferredRun} interrupt={pendingInterrupt} onDone={refresh} />
              : latestDeck
                ? <DeckGallery projectId={projectId} deck={latestDeck} onRunCreated={setActiveRun} />
                : approvedPlanning
                  ? <ProductionLaunch projectId={projectId} planningRunId={approvedPlanning.id} onCreated={setActiveRun} />
                  : <div className="empty"><div><p className="eyebrow">Ready when you are</p><h2>从左侧开始</h2><p>上传资料、描述意图，Agent 会在生成图片前邀请你确认完整页面规划。</p></div></div>}
          </div>
        </div>}
  </section>;
}

function ProductionLaunch({ projectId, planningRunId, onCreated }: { projectId: string; planningRunId: string; onCreated: (runId: string) => void }) {
  const cache = useQueryClient();
  const launch = useMutation({
    mutationFn: () => apiClient.request<{ run_id: string }>(`/api/v1/projects/${projectId}/production-runs`, {
      method: "POST",
      body: { planning_run_id: planningRunId },
    }),
    onSuccess: ({ run_id }) => {
      onCreated(run_id);
      void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
    },
  });
  return <div className="empty"><div>
    <p className="eyebrow">Plan approved</p>
    <h2>规划已批准</h2>
    <p>确认后开始生成整套演示文稿。云模式下此操作会调用图片与视觉模型并产生费用。</p>
    <button className="button primary" disabled={launch.isPending} onClick={() => launch.mutate()}>
      {launch.isPending ? "正在启动…" : "开始生成 PPT"}
    </button>
    {launch.isError && <div className="error-banner">启动生成失败，请刷新后重试。</div>}
  </div></div>;
}
