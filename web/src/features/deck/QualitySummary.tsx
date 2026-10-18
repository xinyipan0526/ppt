import type { QualityReport } from "../../api/contracts";
import { Icon } from "../../components/Icon";
export function QualitySummary({ quality, compact = false }: { quality: QualityReport | null; compact?: boolean }) {
  if (!quality) return <div className="quality-summary neutral"><Icon name="clock" /><span>质检尚未完成</span></div>;
  const errors = quality.issues.filter((issue) => issue.severity === "error"); const warnings = quality.issues.filter((issue) => issue.severity === "warning");
  const label = errors.length || !quality.passed ? "质检未通过" : warnings.length ? "质检有警告" : "质检通过"; const tone = errors.length || !quality.passed ? "danger" : warnings.length ? "warning" : "success";
  return <div className={`quality-summary ${tone}`}><div className="quality-heading"><Icon name={tone === "success" ? "check" : "warning"} /><strong>{label}</strong></div>{!compact && quality.issues.length > 0 && <ul>{quality.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.summary}</li>)}</ul>}</div>;
}
