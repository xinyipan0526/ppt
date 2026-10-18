import type { IconName } from "../../components/Icon";
export type StatusTone = "neutral" | "info" | "waiting" | "warning" | "danger" | "success";
type StatusPresentation = { label: string; tone: StatusTone; icon: IconName; guidance?: string };
const statuses: Record<string, StatusPresentation> = {
  queued: { label: "排队中", tone: "info", icon: "clock" }, running: { label: "正在运行", tone: "info", icon: "refresh" },
  waiting_for_user: { label: "等待确认", tone: "waiting", icon: "pause", guidance: "请完成当前表单后继续。" }, waiting_for_retry: { label: "等待自动重试", tone: "waiting", icon: "clock", guidance: "系统会按已保存的重试策略继续。" },
  recovery_required: { label: "需要人工恢复", tone: "danger", icon: "warning", guidance: "请核对 Provider 结果，逐条处理未知操作后恢复任务。" }, cancel_requested: { label: "正在取消", tone: "warning", icon: "pause" },
  cancelled: { label: "已取消", tone: "neutral", icon: "close" }, failed: { label: "失败", tone: "danger", icon: "error" }, succeeded: { label: "已完成", tone: "success", icon: "check" }, ready: { label: "已就绪", tone: "success", icon: "check" }, available: { label: "可下载", tone: "success", icon: "check" },
};
export function getStatusPresentation(status: string): StatusPresentation { return statuses[status] ?? { label: "未知状态", tone: "neutral", icon: "info" }; }
