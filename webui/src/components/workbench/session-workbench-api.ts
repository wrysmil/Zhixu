import { request } from "@/lib/api";

export type WorkbenchTaskStatus = "pending" | "in_progress" | "completed" | "blocked" | "cancelled";
export interface WorkbenchTask {
  id: string;
  title: string;
  status: WorkbenchTaskStatus;
  source: string;
  detail?: string;
}
export interface SessionWorkbenchSnapshot {
  schema_version: 1;
  session_key: string;
  updated_at: string | null;
  goal: { objective: string; status: string; ui_summary?: string; recap?: string } | null;
  tasks: WorkbenchTask[];
  plan: { explanation: string; steps: WorkbenchTask[]; updated_at: string | null } | null;
  agents: { id: string; name: string; status: string; detail: string; invocation_id: string }[];
  memories: { id: string; kind: "working" | "long_term"; title: string; content: string; source: string; updated_at: string | null; persisted: boolean }[];
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
      && (row.kind === "working" || row.kind === "long_term") && typeof row.persisted === "boolean" && nullableString(row.updated_at))
    && rows(data.skills, (row) => string(row.name) && string(row.source) && string(row.status) && ["available", "always_loaded", "used"].includes(row.status))
    && rows(data.mcp, (row) => string(row.name) && string(row.status) && count(row.calls) && nullableCount(row.successes) && nullableCount(row.failures))
    && Array.isArray(data.limitations) && data.limitations.every(string);
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
