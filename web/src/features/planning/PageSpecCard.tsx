import type { PlanningPageSpec } from "../../api/contracts";

export function PageSpecCard({ page, selected, disabled = false, onToggle }: { page: PlanningPageSpec; selected: boolean; disabled?: boolean; onToggle: () => void }) {
  const index = Number(page.page_index ?? 0);
  return <article className={`card panel page-spec-card${selected ? " selected" : ""}`}><div className="toolbar"><input type="checkbox" disabled={disabled} checked={selected} onChange={onToggle} /><span className="badge">第 {index} 页</span><strong>{String(page.title ?? page.objective ?? "未命名页面")}</strong></div><p className="muted card-copy">{String(page.objective ?? page.page_type ?? "")}</p></article>;
}
