export type IconName = "check" | "clock" | "pause" | "warning" | "error" | "close" | "refresh" | "trash" | "sparkles" | "settings" | "info";

const paths: Record<IconName, string> = {
  check: "M5 12l4 4L19 6", clock: "M12 7v5l3 2 M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
  pause: "M9 7v10 M15 7v10", warning: "M12 4 3 20h18L12 4Zm0 6v4m0 3h.01",
  error: "M7 7l10 10M17 7 7 17", close: "M7 7l10 10M17 7 7 17",
  refresh: "M19 8a7 7 0 1 0 1 6M19 4v4h-4",
  trash: "M6 7h12m-9 0V5h6v2m-8 0 1 13h8l1-13M10 11v5m4-5v5",
  sparkles: "m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3Zm6 10 .7 2.3L21 16l-2.3.7L18 19l-.7-2.3L15 16l2.3-.7L18 13Z",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0-6v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1",
  info: "M12 11v6m0-10h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
};

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return <svg className="icon" aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}
