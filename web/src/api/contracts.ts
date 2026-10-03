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
export interface ModelRuntimeSnapshot { mode: "cloud" | "deterministic"; activation: "active" | "pending"; adapter_id: string; adapter_status: { configured: boolean; backend: string }; multimodal_model: ModelCatalogItem | null; image_model: ModelCatalogItem | null; configuration_version: number; pricing_overrides: Record<string, number>; legacy_configuration_detected: boolean; }
export interface RunSnapshot { run: { id: string; status: RunStatus; recovery_metadata?: Record<string, unknown> | null }; steps: Array<{ id: string; status: string; values: Record<string, unknown> }>; pages: Array<{ id: string; status: string; values: Record<string, unknown> }>; interrupts: Array<{ id: string; status: string; values: Record<string, unknown> }>; snapshot_seq: number; }
export interface PlanDetail { run_id: string; brief: Record<string, unknown> | null; outline: Record<string, unknown> | null; design_system: Record<string, unknown> | null; pages: Array<Record<string, unknown>>; review_ready: boolean; bundle_ready: boolean; }
export interface PageGeneration { id: string; generation: number; status: string; source_generation_id: string | null; delivery_text: Array<{ id: string; role: string; text: string }>; design_prompt: string; local_edit_prompt: string | null; spec: Record<string, unknown> | null; quality: Record<string, unknown> | null; page_image_url: string | null; thumbnail_url: string | null; created_at: string; }
export interface PageDetail { id: string; run_id: string; page_index: number; status: string; stage: string | null; error: unknown; current_generation: PageGeneration; }
