import { request } from "@/lib/api";
import type { WorkbenchLiveFields, Scope, SkillOrigin, Page, NoteSummary, SkillSummary, NotePreview } from "./workbench-live-types";

export type WorkbenchTaskStatus = "pending" | "in_progress" | "completed" | "blocked" | "cancelled";
export interface WorkbenchTask {
  id: string;
  title: string;
  status: WorkbenchTaskStatus;
  source: string;
  detail?: string;
}
export interface SessionWorkbenchSnapshot extends WorkbenchLiveFields {
  schema_version: 1;
  session_key: string;
  updated_at: string | null;
  goal: { objective: string; status: string; ui_summary?: string; recap?: string } | null;
  tasks: WorkbenchTask[];
  plan: { explanation: string; steps: WorkbenchTask[]; updated_at: string | null } | null;
  agents: { id: string; name: string; status: string; detail: string; invocation_id: string }[];
  memories: { id: string; kind: "working" | "long_term"; title: string; content: string; source: string; updated_at: string | null; persisted: boolean; workspace_id?: string; is_global?: boolean }[];
  skills: { name: string; status: "available" | "always_loaded" | "used"; source: string }[];
  mcp: { name: string; status: string; calls: number; successes: number | null; failures: number | null }[];
  limitations: string[];
}

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => !!value && typeof value === "object" && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string";
const nullableString = (value: unknown) => value === null || string(value);
const count = (value: unknown) => typeof value === "number" && Number.isInteger(value) && value >= 0;
const nullableCount = (value: unknown) => value === null || count(value);
const rows = (value: unknown, validate: (row: Row) => boolean) => Array.isArray(value) && value.every((row: unknown) => record(row) && validate(row));
const task = (row: Row) => [row.id, row.title, row.source].every(string)
  && string(row.status) && ["pending", "in_progress", "completed", "blocked", "cancelled"].includes(row.status)
  && (row.detail === undefined || string(row.detail));

function validSnapshot(data: unknown, sessionKey: string): data is SessionWorkbenchSnapshot {
  return record(data) && data.schema_version === 1 && data.session_key === sessionKey && nullableString(data.updated_at)
    && (data.goal === null || (record(data.goal) && string(data.goal.objective) && string(data.goal.status)
      && [data.goal.ui_summary, data.goal.recap].every((value) => value === undefined || string(value))))
    && rows(data.tasks, task)
    && (data.plan === null || (record(data.plan) && string(data.plan.explanation) && rows(data.plan.steps, task) && nullableString(data.plan.updated_at)))
    && rows(data.agents, (row) => [row.id, row.name, row.status, row.detail, row.invocation_id].every(string))
    && rows(data.memories, (row) => [row.id, row.title, row.content, row.source].every(string)
      && (row.kind === "working" || row.kind === "long_term") && typeof row.persisted === "boolean" && nullableString(row.updated_at)
      && (row.workspace_id === undefined || string(row.workspace_id))
      && (row.is_global === undefined || typeof row.is_global === "boolean"))
    && rows(data.skills, (row) => string(row.name) && string(row.source) && string(row.status) && ["available", "always_loaded", "used"].includes(row.status))
    && rows(data.mcp, (row) => string(row.name) && string(row.status) && count(row.calls) && nullableCount(row.successes) && nullableCount(row.failures))
    && Array.isArray(data.limitations) && data.limitations.every(string)
    && validLive(data);
}

export async function fetchSessionWorkbench(token: string, sessionKey: string, signal: AbortSignal): Promise<SessionWorkbenchSnapshot> {
  const data = await request<unknown>(
    `/api/sessions/${encodeURIComponent(sessionKey)}/workbench`, token,
    { signal, cache: "no-store" }, 15_000,
  );
  if (!validSnapshot(data, sessionKey)) {
    throw new Error("工作台返回的数据格式不正确");
  }
  return data;
}

const bool = (v: unknown) => typeof v === "boolean";
const one = (v: unknown, options: string[]) => string(v) && options.includes(v);
const scope = (v: unknown) => one(v, ["workspace", "global"]);
const origin = (v: unknown) => one(v, ["workspace", "global", "plugin", "builtin"]);
const coverage = (v: unknown) => one(v, ["turn", "session", "unrecorded"]);
const resource = (v: unknown): boolean => record(v) && [v.id, v.name, v.title].every(string) && one(v.kind, ["note", "index", "profile", "skill"]) && origin(v.scope);
const observation = (v: unknown): boolean => record(v) && string(v.id) && count(v.sequence) && nullableString(v.turn_id) && nullableString(v.call_id) && nullableString(v.at)
  && one(v.kind, ["phase", "tool", "memory_read", "memory_write", "index_maintenance", "context_provided", "skill_read"])
  && one(v.status, ["running", "succeeded", "failed", "cancelled"]) && string(v.label) && (v.resource === null || resource(v.resource))
  && (v.extent === null || one(v.extent, ["full", "partial", "unknown"])) && (v.write_kind === null || one(v.write_kind, ["created", "updated", "written"]))
  && one(v.source, ["runtime", "history", "extraction"]) && coverage(v.coverage) && (v.turn_id !== null || v.coverage !== "turn");
const note = (v: unknown): boolean => record(v) && [v.id, v.name, v.title, v.description, v.memory_type].every(string) && scope(v.scope) && nullableString(v.updated_at) && bool(v.preview_available);
const skill = (v: unknown): boolean => record(v) && [v.id, v.name, v.description, v.source_label].every(string) && origin(v.scope) && origin(v.origin) && bool(v.effective)
  && one(v.availability, ["available", "disabled", "requirements_missing", "invalid"]) && one(v.observed_state, ["available", "provided", "read", "unrecorded"])
  && nullableString(v.overridden_by) && Array.isArray(v.requirement_labels) && v.requirement_labels.every(string);
const page = (v: unknown, item: (x: unknown) => boolean): boolean => record(v) && Array.isArray(v.items) && v.items.every(item) && count(v.total) && nullableString(v.next_cursor) && string(v.revision);
const obsList = (v: unknown) => Array.isArray(v) && v.every(observation);
function validLive(v: Row): boolean {
  if (v.scope !== undefined && !(record(v.scope) && nullableString(v.scope.project_id) && nullableString(v.scope.project_name) && nullableString(v.scope.turn_id) && coverage(v.scope.coverage) && bool(v.scope.truncated))) return false;
  if (v.activity !== undefined && !(record(v.activity) && one(v.activity.phase, ["processing", "tool_running", "answering", "waiting_user", "completed", "failed", "interrupted", "unknown"]) && nullableString(v.activity.current_action) && nullableString(v.activity.started_at) && obsList(v.activity.events))) return false;
  if (v.memory_context !== undefined) {
    const m = v.memory_context;
    if (!(record(m) && one(m.mode, ["file", "database"]) && coverage(m.coverage) && bool(m.truncated) && obsList(m.provided) && obsList(m.reads) && obsList(m.writes) && obsList(m.indexes) && record(m.catalogs) && page(m.catalogs.workspace, note) && page(m.catalogs.global, note) && record(m.profile) && bool(m.profile.available) && (m.profile.resource === null || resource(m.profile.resource)))) return false;
  }
  if (v.skill_catalog !== undefined) {
    const c = v.skill_catalog;
    if (!(record(c) && count(c.available_total) && record(c.groups) && ["workspace", "global", "plugin", "builtin"].every(k => page((c.groups as Row)[k], skill)))) return false;
  }
  return true;
}
async function liveRequest<T>(url: string, token: string, signal: AbortSignal, valid: (v: unknown) => boolean): Promise<T> {
  const value = await request<unknown>(url, token, { signal, cache: "no-store" }, 15_000);
  if (!valid(value)) throw new Error("工作台返回的数据格式不正确");
  return value as T;
}
const baseUrl = (key: string) => `/api/sessions/${encodeURIComponent(key)}/workbench`;
export function fetchWorkbenchMemories(token: string, key: string, group: Scope, cursor: string | null, signal: AbortSignal): Promise<Page<NoteSummary>> {
  const query = new URLSearchParams({ scope: group, limit: "50" }); if (cursor) query.set("cursor", cursor);
  return liveRequest(`${baseUrl(key)}/memories?${query}`, token, signal, v => page(v, note));
}
export function fetchWorkbenchSkills(token: string, key: string, group: SkillOrigin, cursor: string | null, signal: AbortSignal): Promise<Page<SkillSummary>> {
  const query = new URLSearchParams({ scope: group, limit: "50" }); if (cursor) query.set("cursor", cursor);
  return liveRequest(`${baseUrl(key)}/skills?${query}`, token, signal, v => page(v, skill));
}
export function fetchWorkbenchResource(token: string, key: string, id: string, signal: AbortSignal): Promise<NotePreview> {
  return liveRequest(`${baseUrl(key)}/resources/${encodeURIComponent(id)}`, token, signal, v => record(v) && resource(v.resource) && string(v.content) && nullableString(v.updated_at) && bool(v.truncated));
}
