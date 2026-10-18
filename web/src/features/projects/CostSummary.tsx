import type { ProjectCosts } from "../../api/contracts";
function formatMinor(amount: number, currency: string) { return new Intl.NumberFormat("zh-CN", { style: "currency", currency, minimumFractionDigits: 2 }).format(amount / 100); }
export function CostSummary({ costs }: { costs: ProjectCosts }) {
  if (costs.currencies.length === 0) return <section className="cost-summary muted" aria-label="项目成本">尚未设置项目预算</section>;
  return <section className="cost-summary" aria-label="项目成本">{costs.currencies.map((item) => <article className="cost-currency" key={item.currency}><strong>{item.currency}</strong><dl className="summary-grid"><div><dt>已结算</dt><dd>{formatMinor(item.settled, item.currency)}</dd></div><div><dt>预留</dt><dd>{formatMinor(item.active_reserved, item.currency)}</dd></div><div><dt>未知费用</dt><dd>{formatMinor(item.held_unknown, item.currency)}</dd></div><div><dt>可用</dt><dd>{formatMinor(item.available, item.currency)}</dd></div></dl></article>)}</section>;
}
