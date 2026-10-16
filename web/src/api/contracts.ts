export type RunStatus = "queued" | "running" | "waiting_for_user" | "waiting_for_retry" | "recovery_required" | "cancel_requested" | "cancelled" | "failed" | "succeeded";

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: string;
  latest_run: { id: string; workflow: string; status: RunStatus } | null;
  material_count: number;
  deck_generation_count: number;
}
export interface WorkspaceSummary { projects: ProjectSummary[]; }
export interface MaterialItem { id: string; filename: string; display_name?: string; media_type: string; status: string; version?: number; error?: unknown; created_at: string; }
export interface RunItem { id: string; workflow: string; status: RunStatus; created_at: string; }
export interface DeckGeneration { id: string; project_id: string; planning_run_id: string; production_run_id: string; generation: number; status: RunStatus; source_generation_id: string | null; instruction: string | null; page_generation_ids: string[]; created_at: string; }
export interface ProjectWorkspaceData { project: { id: string; name: string }; runs: RunItem[]; materials: MaterialItem[]; deck_generations: DeckGeneration[]; }
export interface ModelCatalogItem { adapter_id: string; model_id: string; display_name: string; category: "multimodal" | "image"; lifecycle: "stable" | "experimental" | "deprecated"; capabilities: string[]; recommended: boolean; pricing: Record<string, string | number>; }
export interface ModelRuntimeSnapshot { mode: "cloud" | "deterministic"; activation: "active" | "pending"; adapter_id: string; adapter_status: { configured: boolean; backend: string }; multimodal_model: ModelCatalogItem | null; image_model: ModelCatalogItem | null; configuration_version: number; pricing_overrides: Record<string, number>; legacy_configuration_detected: boolean; setup_required: boolean; }
export interface CancelRunResponse { run_id: string; status: RunStatus; accepted: boolean; }
export interface RunSnapshot { run: { id: string; status: RunStatus; error?: SafeRunError | null; retry_at?: string | null; recovery_metadata?: Record<string, unknown> | null }; steps: Array<{ id: string; status: string; values: Record<string, unknown> }>; pages: Array<{ id: string; status: string; values: Record<string, unknown> }>; interrupts: Array<{ id: string; status: string; values: Record<string, unknown> }>; snapshot_seq: number; }
export interface SafeProviderSummary {
  http_status?: number;
  provider_error_type?: string;
  request_phase?: "connect" | "write" | "read" | "parse" | "validate";
  timeout_seconds?: number;
  elapsed_seconds?: number;
  provider_response_received?: boolean;
  provider_request_id?: string;
  provider_error_message?: string;
}
export interface SafeRunError { code: string; provider_summary?: SafeProviderSummary | null; }
export type ExportStatus = "queued" | "running" | "available" | "failed" | "cancelled";
export type ExportFormat = "pptx" | "pdf" | "png_zip" | "jpg_zip" | "png" | "jpg";
export interface ExportError { code: string; detail?: string; }
export interface ExportSnapshot {
  id: string;
  project_id: string;
  run_id: string;
  format: ExportFormat;
  status: ExportStatus;
  artifact_id: string | null;
  error: ExportError | null;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
}
export interface RunTrace {
  run: { id: string; project_id: string; workflow_name: string; workflow_version: number; status: RunStatus; error: SafeRunError | null; };
  steps: Array<{ id: string; status: string; step_key: string; generation: number; attempt_count: number; error: SafeRunError | null; }>;
  events: Array<{ event_id: string; run_seq: number; event_type: string; schema_version: number; occurred_at: string; recorded_at: string; step_id: string | null; page_id: string | null; operation_id: string | null; trace_id: string | null; durability: string; }>;
  operations: Array<{ operation: { id: string; operation_kind: string; provider: string; model: string; aggregate_status: string; selected_attempt_id: string | null; has_unknown_attempts: boolean; step_id: string | null; page_id: string | null; }; attempts: Array<{ id: string; attempt_no: number; status: string; retry_authorized: boolean; request_started_at: string | null; response_received_at: string | null; error_code: string | null; response_summary: SafeProviderSummary | null; }>; }>;
}
export interface CostCurrencySummary { currency: string; hard_limit: number; settled: number; active_reserved: number; held_unknown: number; available: number; }
export interface ProjectCosts { project_id: string; currencies: CostCurrencySummary[]; totals: Array<{ provider: string; currency: string; actual_amount: number }>; }
export interface PresentationBrief { topic: string; purpose: string | null; audience: string | null; language: "zh-CN" | "en-US"; tone: string | null; page_count: number; aspect_ratio: "16:9"; budget_minor_units: number | null; quality: "draft" | "balanced" | "high"; }
export interface OutlinePage { page_index: number; objective: string; section: string | null; }
export interface DeckOutline { narrative: string; pages: OutlinePage[]; }
export interface DesignSystem { color_palette: string[]; typography: Record<string, string>; visual_style: string; image_style: string; spacing_scale: number[]; prohibited: string[]; }
export interface PlanningPageSpec { page_index: number; objective: string; page_type: "cover" | "section" | "content" | "data" | "closing"; title: string | null; body_blocks: string[]; layout_intent: string | null; visual_requirements: string[]; source_refs: string[]; acceptance_criteria: string[]; }
export interface PlanDetail { run_id: string; brief: PresentationBrief | null; outline: DeckOutline | null; design_system: DesignSystem | null; pages: PlanningPageSpec[]; review_ready: boolean; bundle_ready: boolean; }
export interface NormalizedBox { x: number; y: number; width: number; height: number; }
export interface PageEditSummary { id: string; status: string; kind: string; clarification_question: string | null; target_box: NormalizedBox | null; }
export interface QualityIssue { code: string; severity: "warning" | "error"; element_id?: string | null; summary: string; }
export interface QualityReport { schema_version?: number; page_id?: string; passed: boolean; issues: QualityIssue[]; }
export interface PageGeneration { id: string; generation: number; status: string; source_generation_id: string | null; delivery_text: Array<{ id: string; role: string; text: string }>; design_prompt: string; local_edit_prompt: string | null; spec: Record<string, unknown> | null; quality: QualityReport | null; page_image_url: string | null; thumbnail_url: string | null; created_at: string; }
export interface PageDetail { id: string; run_id: string; page_index: number; status: string; stage: string | null; error: unknown; current_generation: PageGeneration; pending_edit: PageEditSummary | null; }
