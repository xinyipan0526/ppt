import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { WorkspaceSummary } from "../../api/contracts";

export function ProjectList() {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["workspace"], queryFn: () => apiClient.request<WorkspaceSummary>("/api/v1/workspace") });
  const create = useMutation({ mutationFn: () => apiClient.request<{ id: string }>("/api/v1/projects", { method: "POST", body: { name } }), onSuccess: (project) => { void cache.invalidateQueries({ queryKey: ["workspace"] }); navigate(`/projects/${project.id}`); } });
  return <section>
    <header className="page-head"><div><p className="eyebrow">Project workspace</p><h1>把想法变成完整演示</h1><p className="muted">资料、规划、生成、修改与导出，都在一个可恢复的工作台里。</p></div><button className="button primary" onClick={() => setCreating(true)}>＋ 新建项目</button></header>
    {query.isLoading && <div className="grid projects"><div className="skeleton" /><div className="skeleton" /></div>}
    {query.error && <div className="error-banner">无法读取项目，请确认本地服务已启动。</div>}
    {query.data?.projects.length === 0 && <div className="empty"><div><h2>还没有项目</h2><p>创建一个项目，从文字、Markdown、PDF 或参考图开始。</p><button className="button primary" onClick={() => setCreating(true)}>创建第一个项目</button></div></div>}
    <div className="grid projects">{query.data?.projects.map((project) => <Link className="card project-card" key={project.id} to={`/projects/${project.id}`}><div><span className={`badge ${project.latest_run?.status ?? ""}`}>{project.latest_run?.status ?? "尚未开始"}</span><h2 style={{ marginTop: 14 }}>{project.name}</h2><p className="muted">{project.deck_generation_count ? `${project.deck_generation_count} 个成品版本` : "等待首次生成"}</p></div><div className="meta"><span>{project.material_count} 份资料</span><span>打开工作台 →</span></div></Link>)}</div>
    {creating && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="新建项目"><form className="modal card" onSubmit={(event) => { event.preventDefault(); if (name.trim()) create.mutate(); }}><p className="eyebrow">New project</p><h2>给这份演示起个名字</h2><div className="field"><label htmlFor="project-name">项目名称</label><input id="project-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：2027 产品战略" maxLength={255} /></div><div className="toolbar end" style={{ marginTop: 22 }}><button type="button" className="button" onClick={() => setCreating(false)}>取消</button><button className="button primary" disabled={!name.trim() || create.isPending}>创建并进入</button></div></form></div>}
  </section>;
}
