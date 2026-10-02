import { NavLink, Route, Routes } from "react-router-dom";
import { ProviderSetup } from "../features/settings/ProviderSetup";
import { ProjectList } from "../features/projects/ProjectList";
import { ProjectWorkspace } from "../features/projects/ProjectWorkspace";

export function App() {
  return (
    <div className="app-shell">
      <aside className="rail">
        <NavLink to="/" className="brand" aria-label="PPT Agent 首页"><span className="brand-mark">P</span><span>PPT Agent</span></NavLink>
        <nav aria-label="主导航">
          <NavLink to="/" end>项目</NavLink>
          <NavLink to="/settings">模型设置</NavLink>
        </nav>
        <div className="rail-foot"><span className="status-dot" />本地优先 · 自动保存</div>
      </aside>
      <main className="main-stage">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/settings" element={<ProviderSetup />} />
          <Route path="/projects/:projectId" element={<ProjectWorkspace />} />
        </Routes>
      </main>
    </div>
  );
}
