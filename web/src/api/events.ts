export type RunEvent = { type: string; data: unknown; id?: string };

export class RunEventStream {
  private source: EventSource | null = null;
  private pollTimer: number | null = null;
  constructor(private readonly runId: string) {}

  connect(onEvent: (event: RunEvent) => void, onRefresh: () => void): () => void {
    this.source = new EventSource(`/api/v1/runs/${encodeURIComponent(this.runId)}/events`, { withCredentials: true });
    this.source.onmessage = (event) => onEvent({ type: "message", data: this.parse(event.data), id: event.lastEventId });
    for (const type of RUN_EVENT_TYPES) {
      this.source.addEventListener(type, (raw) => {
        const event = raw as MessageEvent<string>;
        onEvent({ type, data: this.parse(event.data), id: event.lastEventId });
      });
    }
    this.source.addEventListener("cursor_expired", () => onRefresh());
    this.source.addEventListener("run_unavailable", () => onRefresh());
    this.source.onerror = () => {
      this.source?.close();
      if (this.pollTimer === null) this.pollTimer = window.setInterval(onRefresh, 3_000);
    };
    return () => this.close();
  }

  close(): void {
    this.source?.close();
    this.source = null;
    if (this.pollTimer !== null) window.clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  private parse(value: string): unknown {
    try { return JSON.parse(value); } catch { return value; }
  }
}

const RUN_EVENT_TYPES = [
  "run.queued",
  "run.run_dispatched",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.recovery_required",
  "step.created",
  "step.running",
  "step.completed",
  "step.failed",
  "interrupt.created",
  "interrupt.resumed",
  "planning.approved",
  "planning.revision_requested",
  "page.page_started",
  "page.page_completed",
  "run.all_required_steps_succeeded",
] as const;
