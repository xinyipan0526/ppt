import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { ProjectCosts, ProjectWorkspaceData, RunSnapshot } from "../../api/contracts";
import { MaterialPanel } from "../materials/MaterialPanel";
import { PlanningInput } from "../planning/PlanningInput";
import { ClarificationForm } from "../planning/ClarificationForm";
import { PlanReview } from "../planning/PlanReview";
import { DeckGallery } from "../deck/DeckGallery";
import { RunStatus } from "../runs/RunStatus";
import { ErrorBanner } from "../../components/ErrorBanner";
import { CostSummary } from "./CostSummary";
import { DeckGenerationPicker } from "../deck/DeckGenerationPicker";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
type ProviderSettings = {
  mode: "cloud" | "deterministic";
  configured: boolean;
  setup_required: boolean;
};

export function ProjectWorkspace() {
  const projectId = useParams().projectId!;
  const cache = useQueryClient();
  const [selectedMaterials, setSelectedMaterials] = useState<string[]>([]);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const [dismissedRuns, setDismissedRuns] = useState<string[]>([]);
  const [selectedDeckId, setSelectedDeckId] = useState<string | null>(null);
  const workspace = useQuery({
    queryKey: ["project-workspace", projectId],
    queryFn: () => apiClient.request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/workspace`),
    refetchInterval: activeRun ? 4_000 : false,
  });
  const providerSettings = useQuery({
    queryKey: ["provider-settings"],
    queryFn: () => apiClient.request<ProviderSettings>("/api/v1/provider-settings"),
  });
  const costs = useQuery({ queryKey: ["project-costs", projectId], queryFn: () => apiClient.request<ProjectCosts>(`/api/v1/projects/${projectId}/costs`) });
  const inferredRun = activeRun
    ?? workspace.data?.runs.find((run) => !TERMINAL.has(run.status) && !dismissedRuns.includes(run.id))?.id
    ?? workspace.data?.runs.find((run) => !dismissedRuns.includes(run.id))?.id
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
  const selectedDeck = workspace.data?.deck_generations.find((deck) => deck.id === selectedDeckId) ?? latestDeck;
  const approvedPlanning = workspace.data?.runs.find(
    (run) => run.workflow === "deck_planning" && run.status === "succeeded",
  );
  const setupRequired = providerSettings.data?.setup_required === true;
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
    if (inferredRun) void cache.invalidateQueries({ queryKey: ["run-snapshot", inferredRun] });
  };

  useEffect(() => {
    if (activeRun && snapshot.data && TERMINAL.has(snapshot.data.run.status)) {
      void cache.invalidateQueries({ queryKey: ["project-workspace", projectId] });
    }
  }, [activeRun, cache, projectId, snapshot.data]);

  if (workspace.isLoading) return <div className="skeleton" />;
  if (!workspace.data) {
    return <div className="error-banner">项目不存在或暂时无法读取。<Link to="/">返回项目</Link></div>;
  }
  if (providerSettings.isFetching && !providerSettings.data) {
    return <WorkspaceConfigurationState message="正在检查模型设置…" />;
  }
  if (providerSettings.isError && !providerSettings.data) {
    return <WorkspaceConfigurationState message="无法读取模型设置，请检查本地服务后重试。" error />;
  }
  if (!providerSettings.data) return <WorkspaceConfigurationState message="无法读取模型设置，请检查本地服务后重试。" error />;
  const paidActionsDisabled = setupRequired || providerSettings.isFetching || providerSettings.isError;
  const isReview = pendingInterrupt
    && (pendingInterrupt.values.form_schema as Record<string, unknown> | undefined)?.kind === "plan_review";

  return <section>
    <div className="toolbar breadcrumb">
      <Link className="muted" to="/">项目</Link><span className="muted">/</span><strong>{workspace.data.project.name}</strong>
    </div>
    {costs.data && <CostSummary costs={costs.data} />}
    {setupRequired && <ProviderSetupRequired />}
    {isReview && inferredRun
      ? <PlanReview projectId={projectId} runId={inferredRun} interrupt={pendingInterrupt} disabled={paidActionsDisabled} onApproved={refresh} />
      : <div className="workspace-layout">
          <aside className="side-stack">
            <MaterialPanel projectId={projectId} materials={workspace.data.materials} selected={selectedMaterials} onSelected={setSelectedMaterials} />
            <PlanningInput projectId={projectId} materialIds={selectedMaterials} disabled={paidActionsDisabled} onCreated={(runId) => { setDismissedRuns((runs) => runs.filter((id) => id !== runId)); setActiveRun(runId); }} />
            {inferredRun && <RunStatus runId={inferredRun} paidActionsDisabled={paidActionsDisabled} onChange={refresh} onDismiss={() => { setDismissedRuns((runs) => [...runs, inferredRun]); if (activeRun === inferredRun) setActiveRun(null); }} />}
          </aside>
          <div>
            {pendingInterrupt && inferredRun
              ? <ClarificationForm runId={inferredRun} interrupt={pendingInterrupt} disabled={paidActionsDisabled} onDone={refresh} />
              : selectedDeck
                ? <><DeckGenerationPicker projectId={projectId} generations={workspace.data.deck_generations} selectedId={selectedDeck.id} onSelect={setSelectedDeckId} onRestored={(deck) => setSelectedDeckId(deck.id)} /><DeckGallery projectId={projectId} deck={selectedDeck} disabled={paidActionsDisabled || selectedDeck.id !== latestDeck?.id} onRunCreated={(runId) => { setDismissedRuns((runs) => runs.filter((id) => id !== runId)); setActiveRun(runId); }} /></>
                : approvedPlanning
                  ? <ProductionLaunch projectId={projectId} planningRunId={approvedPlanning.id} disabled={paidActionsDisabled} onCreated={(runId) => { setDismissedRuns((runs) => runs.filter((id) => id !== runId)); setActiveRun(runId); }} />
                  : <div className="empty"><div><p className="eyebrow">Ready when you are</p><h2>从左侧开始</h2><p>上传资料、描述意图，Agent 会在生成图片前邀请你确认完整页面规划。</p></div></div>}
          </div>
        </div>}
  </section>;
}

function WorkspaceConfigurationState({ message, error = false }: { message: string; error?: boolean }) {
  return <section className={`card panel${error ? " error-banner" : ""}`} role={error ? "alert" : "status"} aria-live="polite">
    {message}
  </section>;
}

function ProviderSetupRequired() {
  return <section className="card panel error-banner" role="alert" aria-live="assertive">
    <h2>请先完成模型设置</h2>
    <p>云模式需要一个 Ark API Key，以及多模态和图片模型，才能开始生成并产生费用。</p>
    <Link className="button primary" to="/settings">前往模型设置</Link>
  </section>;
}

function ProductionLaunch({ projectId, planningRunId, onCreated, disabled = false }: { projectId: string; planningRunId: string; onCreated: (runId: string) => void; disabled?: boolean }) {
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
    <button className="button primary" disabled={disabled || launch.isPending} onClick={() => launch.mutate()}>
      {launch.isPending ? "正在启动…" : "开始生成 PPT"}
    </button>
    {launch.error && <ErrorBanner error={launch.error} fallback="启动生成失败，请刷新后重试。" />}
  </div></div>;
}
