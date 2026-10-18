export function ProgressBar({ value, max, label }: { value?: number; max?: number; label: string }) {
  const determinate = typeof value === "number" && typeof max === "number" && max > 0;
  const percentage = determinate ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const shortLabel = label.split(/[：:]/, 1)[0];
  const visibleLabel = label.replace("，", " · ");
  return <div className="progress-block" aria-label={shortLabel === label ? undefined : shortLabel}><div className="progress-label">{visibleLabel}</div><div className={`progress-track${determinate ? "" : " indeterminate"}`} role="progressbar" aria-label={label} {...(determinate ? { "aria-valuenow": value, "aria-valuemin": 0, "aria-valuemax": max } : {})}><span className="progress-value" style={determinate ? { width: `${percentage}%` } : undefined} /></div></div>;
}
