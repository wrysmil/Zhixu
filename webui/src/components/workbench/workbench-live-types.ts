export type Scope = "workspace" | "global";
export type SkillOrigin = Scope | "plugin" | "builtin";
export type Coverage = "turn" | "session" | "unrecorded";
export type Phase = "processing" | "tool_running" | "answering" | "waiting_user" | "completed" | "failed" | "interrupted" | "unknown";
export interface Page<T> { items: T[]; total: number; next_cursor: string | null; revision: string }
export interface ResourceRef { id: string; kind: "note" | "index" | "profile" | "skill"; scope: SkillOrigin; name: string; title: string }
export interface Observation {
  id: string; sequence: number; turn_id: string | null; call_id: string | null; at: string | null;
  kind: "phase" | "tool" | "memory_read" | "memory_write" | "index_maintenance" | "context_provided" | "skill_read";
  status: "running" | "succeeded" | "failed" | "cancelled"; label: string; resource: ResourceRef | null;
  extent: "full" | "partial" | "unknown" | null; write_kind: "created" | "updated" | "written" | null;
  source: "runtime" | "history" | "extraction"; coverage: Coverage;
}
export interface NoteSummary { id: string; scope: Scope; name: string; title: string; description: string; memory_type: string; updated_at: string | null; preview_available: boolean }
export interface SkillSummary { id: string; name: string; description: string; scope: SkillOrigin; origin: SkillOrigin; effective: boolean; availability: "available" | "disabled" | "requirements_missing" | "invalid"; observed_state: "available" | "provided" | "read" | "unrecorded"; overridden_by: string | null; source_label: string; requirement_labels: string[] }
export interface WorkbenchScope { project_id: string | null; project_name: string | null; turn_id: string | null; coverage: Coverage; truncated: boolean }
export interface WorkbenchActivity { phase: Phase; current_action: string | null; started_at: string | null; events: Observation[] }
export interface MemoryContext { mode: "file" | "database"; coverage: Coverage; truncated: boolean; provided: Observation[]; reads: Observation[]; writes: Observation[]; indexes: Observation[]; catalogs: { workspace: Page<NoteSummary>; global: Page<NoteSummary> }; profile: { available: boolean; resource: ResourceRef | null } }
export interface SkillCatalog { available_total: number; groups: Record<SkillOrigin, Page<SkillSummary>> }
export interface WorkbenchLiveFields { scope?: WorkbenchScope; activity?: WorkbenchActivity; memory_context?: MemoryContext; skill_catalog?: SkillCatalog }
export interface NotePreview { resource: ResourceRef; content: string; updated_at: string | null; truncated: boolean }
