export function PageSpecCard({ page, selected, onToggle }: { page: Record<string, unknown>; selected: boolean; onToggle: () => void }) {
  const index = Number(page.page_index ?? 0);
  return <article className="card panel" style={{ borderColor: selected ? "var(--accent)" : undefined }}><div className="toolbar"><input type="checkbox" checked={selected} onChange={onToggle} /><span className="badge">第 {index} 页</span><strong>{String(page.title ?? page.objective ?? "未命名页面")}</strong></div><p className="muted" style={{ margin: "12px 0 0" }}>{String(page.objective ?? page.page_type ?? "")}</p></article>;
}
