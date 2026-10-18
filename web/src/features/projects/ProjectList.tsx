import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { ProjectSummary, WorkspaceSummary } from "../../api/contracts";
import { Dialog } from "../../components/Dialog";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";

export function ProjectList() {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["workspace"], queryFn: () => apiClient.request<WorkspaceSummary>("/api/v1/workspace") });
  const create = useMutation({ mutationFn: () => apiClient.request<{ id: string }>("/api/v1/projects", { method: "POST", body: { name } }), onSuccess: (project) => { void cache.invalidateQueries({ queryKey: ["workspace"] }); navigate(`/projects/${project.id}`); } });
  const remove = useMutation({ mutationFn: (project: ProjectSummary) => apiClient.request(`/api/v1/projects/${project.id}`, { method: "DELETE" }), onSuccess: () => { setDeleting(null); void cache.invalidateQueries({ queryKey: ["workspace"] }); } });
  return <section>
    <header className="page-head"><div><p className="eyebrow">Project workspace</p><h1>把想法变成完整演示</h1><p className="muted">资料、规划、生成、修改与导出，都在一个可恢复的工作台里。</p></div><button className="button primary" onClick={() => setCreating(true)}><Icon name="sparkles" />新建项目</button></header>
    {query.isLoading && <div className="grid projects"><div className="skeleton" /><div className="skeleton" /></div>}
    {query.error && <ErrorBanner error={query.error} fallback="无法读取项目，请确认本地服务已启动。" />}
    {query.data?.projects.length === 0 && <div className="empty onboarding"><div><p className="eyebrow">首次使用</p><h2>从资料到成品，只需三步</h2><ol><li>检查模型设置；确定性模式无需 API Key，可直接体验。</li><li>创建项目并上传 Markdown、PDF 或参考图。</li><li>确认规划后生成、修改并导出演示文稿。</li></ol><div className="toolbar center"><Link className="button" to="/settings"><Icon name="settings" />检查模型设置</Link><button className="button primary" onClick={() => setCreating(true)}><Icon name="sparkles" />创建第一个项目</button></div></div></div>}
    <div className="grid projects">{query.data?.projects.map((project) => <article className="card project-card" key={project.id}><Link className="project-link" to={`/projects/${project.id}`}><span className={`badge ${project.latest_run?.status ?? ""}`}>{project.latest_run?.status ?? "尚未开始"}</span><h2>{project.name}</h2><p className="muted">{project.deck_generation_count ? `${project.deck_generation_count} 个成品版本` : "等待首次生成"}</p><div className="meta"><span>{project.material_count} 份资料</span><span>打开工作台</span></div></Link><button className="button danger project-delete" aria-label={`删除项目 ${project.name}`} onClick={() => setDeleting(project)}><Icon name="trash" />删除</button></article>)}</div>
    <Dialog title="给这份演示起个名字" open={creating} onClose={() => setCreating(false)} busy={create.isPending} initialFocusRef={nameInputRef}><form onSubmit={(event) => { event.preventDefault(); if (name.trim()) create.mutate(); }}><p className="eyebrow">New project</p><div className="field"><label htmlFor="project-name">项目名称</label><input ref={nameInputRef} id="project-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：2027 产品战略" maxLength={255} /></div><div className="action-row"><button type="button" className="button" disabled={create.isPending} onClick={() => setCreating(false)}>取消</button><button className="button primary" disabled={!name.trim() || create.isPending}>创建并进入</button></div>{create.error && <ErrorBanner error={create.error} fallback="创建项目失败，请稍后重试。" />}</form></Dialog>
    <ConfirmDialog open={Boolean(deleting)} title="删除项目" confirmLabel="确认删除项目" danger busy={remove.isPending} onClose={() => setDeleting(null)} onConfirm={() => { if (deleting) remove.mutate(deleting); }}><p>将删除“{deleting?.name}”及其工作区记录。此操作无法撤销。</p>{remove.error && <ErrorBanner error={remove.error} fallback="项目删除失败，请重试。" />}</ConfirmDialog>
  </section>;
}
