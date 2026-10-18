import { ApiError } from "../api/client";

export function ErrorBanner({ error, fallback }: { error: unknown; fallback: string }) {
  const message = error instanceof ApiError && error.message.trim() ? error.message : fallback;
  return <div className="error-banner" role="alert" aria-live="assertive">{message}</div>;
}
