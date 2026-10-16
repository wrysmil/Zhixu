// 团队事件的线格式解析（契约 §6）。
//
// 真值源是 `nanobot/teams/events.py` 的 `EVENT_DATA_FIELDS` 与
// `tests/fixtures/teams/event.json`：每种 type 的 data 只有一个白名单键。
// 解析器面对任何**不认识**的输入都必须返回 null —— 前端宁可什么都不显示，
// 也不能拿半懂的字段当团队状态（契约 §6 的补拉协议依赖这一点）。

import type {
  LeaderAction,
  TeamBinding,
  TeamRunSnapshot,
  TeamRunStatus,
  TeamTaskSnapshot,
  TaskStatus,
} from "@/lib/teams/types";

export const TEAM_RUN_EVENT_TYPES = [
  "run.started",
  "leader.updated",
  "task.created",
  "task.queued",
  "task.started",
  "task.activity",
  "task.finished",
  "run.cancelling",
  "run.finished",
] as const;

export type TeamRunEventType = (typeof TEAM_RUN_EVENT_TYPES)[number];

export const TEAM_RUN_TERMINAL_STATUSES: readonly TeamRunStatus[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
  "interrupted",
];

export const TEAM_TASK_TERMINAL_STATUSES: readonly TaskStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "interrupted",
];

export interface TeamEventEnvelope {
  schemaVersion: number;
  runId: string;
  chatId: string;
  turnId: string;
  seq: number;
  type: TeamRunEventType;
  taskId: string | null;
  attemptId: string | null;
  occurredAt: string;
}

/** run.finished 带整份 run 快照；run.started 只带绑定与起点（契约 §6 的判别形状）。 */
export interface TeamEventWithRun extends TeamEventEnvelope {
  run: TeamRunSnapshot;
}

export interface TeamRunStartedEvent extends TeamEventEnvelope {
  binding: TeamBinding;
  startedAt: string;
}

export interface TeamTaskEvent extends TeamEventEnvelope {
  task: TeamTaskSnapshot;
}

export interface TeamLeaderEvent extends TeamEventEnvelope {
  leaderAction: LeaderAction;
}

export interface TeamRunCancellingEvent extends TeamEventEnvelope {
  status: "cancelling";
}

export type TeamEvent =
  | TeamEventWithRun
  | TeamRunStartedEvent
  | TeamTaskEvent
  | TeamLeaderEvent
  | TeamRunCancellingEvent;

/** 帧层：``team_event`` / ``team_snapshot`` 的公共外壳。 */
export interface TeamFrameEnvelope {
  event: "team_event" | "team_snapshot";
  chat_id: string;
  turn_id: string;
  data: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function asSequence(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function asStatus<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null;
}

const RUN_STATUSES: readonly TeamRunStatus[] = [
  "running",
  "cancelling",
  ...TEAM_RUN_TERMINAL_STATUSES,
];

const TASK_STATUSES: readonly TaskStatus[] = [
  "pending",
  "queued",
  "running",
  "cancelling",
  ...TEAM_TASK_TERMINAL_STATUSES,
];

const LEADER_ACTIONS: readonly LeaderAction[] = [
  "planning",
  "waiting",
  "reviewing",
  "summarizing",
  "idle",
];

export function isTerminalRunStatus(status: TeamRunStatus): boolean {
  return TEAM_RUN_TERMINAL_STATUSES.includes(status);
}

export function isTerminalTaskStatus(status: TaskStatus): boolean {
  return TEAM_TASK_TERMINAL_STATUSES.includes(status);
}

function parseBinding(value: unknown): TeamBinding | null {
  if (!isRecord(value)) return null;
  const teamId = asText(value.teamId);
  const teamName = asText(value.teamName);
  const revision = asSequence(value.teamRevision);
  if (!teamId || !teamName || revision === null) return null;
  return { teamId, teamName, teamRevision: revision };
}

/**
 * 任务结果的白名单投影。摘要与错误码是 UI 唯一能显示的东西；
 * 完整执行结果不下发（契约 §7 的边界）。
 */
function parseTaskResult(value: unknown): TeamTaskSnapshot["result"] {
  if (!isRecord(value)) return null;
  const taskId = asText(value.taskId);
  const memberId = asText(value.memberId);
  const status = asStatus(value.status, TEAM_TASK_TERMINAL_STATUSES);
  if (!taskId || !memberId || !status) return null;
  const usage = isRecord(value.usage)
    ? {
        inputTokens: asSequence(value.usage.inputTokens) ?? 0,
        outputTokens: asSequence(value.usage.outputTokens) ?? 0,
      }
    : null;
  return {
    taskId,
    attemptId: asText(value.attemptId) ?? "",
    memberId,
    agentId: asText(value.agentId) ?? "",
    status,
    stopReason: typeof value.stopReason === "string" ? value.stopReason : "",
    summary: typeof value.summary === "string" ? value.summary : "",
    artifacts: Array.isArray(value.artifacts)
      ? value.artifacts.filter((item): item is string => typeof item === "string")
      : [],
    errorCode: typeof value.errorCode === "string" ? value.errorCode : null,
    usage,
  };
}

export function parseTaskSnapshot(value: unknown): TeamTaskSnapshot | null {
  if (!isRecord(value)) return null;
  const taskId = asText(value.taskId);
  const memberId = asText(value.memberId);
  const status = asStatus(value.status, TASK_STATUSES);
  if (!taskId || !memberId || !status) return null;
  return {
    taskId,
    memberId,
    agentId: asText(value.agentId) ?? "",
    title: typeof value.title === "string" ? value.title : "",
    acceptance: typeof value.acceptance === "string" ? value.acceptance : "",
    dependsOn: Array.isArray(value.dependsOn)
      ? value.dependsOn.filter((item): item is string => typeof item === "string")
      : [],
    status,
    attemptId: asText(value.attemptId),
    lastAction: typeof value.lastAction === "string" ? value.lastAction : null,
    startedAt: typeof value.startedAt === "string" ? value.startedAt : null,
    finishedAt: typeof value.finishedAt === "string" ? value.finishedAt : null,
    result: parseTaskResult(value.result),
  };
}

export function parseRunSnapshot(value: unknown): TeamRunSnapshot | null {
  if (!isRecord(value)) return null;
  const runId = asText(value.runId);
  const chatId = asText(value.chatId);
  const turnId = asText(value.turnId);
  const binding = parseBinding(value.binding);
  const status = asStatus(value.status, RUN_STATUSES);
  const lastSeq = asSequence(value.lastSeq);
  if (!runId || !chatId || !turnId || !binding || !status || lastSeq === null) return null;
  return {
    runId,
    chatId,
    turnId,
    binding,
    status,
    lastSeq,
    tasks: Array.isArray(value.tasks)
      ? value.tasks
          .map(parseTaskSnapshot)
          .filter((item): item is TeamTaskSnapshot => item !== null)
      : [],
    leaderAction: asStatus(value.leaderAction, LEADER_ACTIONS) ?? "idle",
    startedAt: typeof value.startedAt === "string" ? value.startedAt : "",
    finishedAt: typeof value.finishedAt === "string" ? value.finishedAt : null,
    parentRunId: asText(value.parentRunId),
  };
}

function parseEnvelope(value: unknown): TeamEventEnvelope | null {
  if (!isRecord(value)) return null;
  const schemaVersion = asSequence(value.schemaVersion);
  const runId = asText(value.runId);
  const chatId = asText(value.chatId);
  const turnId = asText(value.turnId);
  const seq = asSequence(value.seq);
  const type = asStatus<TeamRunEventType>(
    value.type,
    TEAM_RUN_EVENT_TYPES,
  );
  if (schemaVersion !== 1 || !runId || !chatId || !turnId || seq === null || !type) {
    return null;
  }
  return {
    schemaVersion,
    runId,
    chatId,
    turnId,
    seq,
    type,
    taskId: asText(value.taskId),
    attemptId: asText(value.attemptId),
    occurredAt: typeof value.occurredAt === "string" ? value.occurredAt : "",
  };
}

/** 解析一条 ``team_event`` 载荷；看不懂就返回 null，调用方必须能安全忽略。 */
export function parseTeamEvent(value: unknown): TeamEvent | null {
  const envelope = parseEnvelope(value);
  if (!envelope || !isRecord(value)) return null;
  const data = value.data;

  if (envelope.type === "run.started") {
    const binding = parseBinding(isRecord(data) ? data.binding : null);
    if (!binding) return null;
    const startedAt = isRecord(data) && typeof data.startedAt === "string" ? data.startedAt : "";
    return { ...envelope, binding, startedAt };
  }
  if (envelope.type === "run.finished") {
    const run = parseRunSnapshot(isRecord(data) ? data.run : null);
    return run ? { ...envelope, run } : null;
  }
  if (
    envelope.type === "task.created"
    || envelope.type === "task.queued"
    || envelope.type === "task.started"
    || envelope.type === "task.activity"
    || envelope.type === "task.finished"
  ) {
    const task = parseTaskSnapshot(isRecord(data) ? data.task : null);
    return task ? { ...envelope, task } : null;
  }
  if (envelope.type === "leader.updated") {
    const action = isRecord(data)
      ? asStatus<LeaderAction>(data.leaderAction, LEADER_ACTIONS)
      : null;
    return action ? { ...envelope, leaderAction: action } : null;
  }
  // run.cancelling
  return { ...envelope, status: "cancelling" };
}

export type TeamFrame =
  | { kind: "event"; chatId: string; turnId: string; event: TeamEvent }
  | { kind: "snapshot"; chatId: string; turnId: string; run: TeamRunSnapshot };

/**
 * 解析 WS 帧。未知 event 名、坏掉的 data、非团队帧一律 null —— 普通聊天不能被
 * 一条看不懂的团队帧影响（契约 §6）。
 */
export function parseTeamFrame(value: unknown): TeamFrame | null {
  if (!isRecord(value)) return null;
  const name = value.event ?? value.type;
  if (name !== "team_event" && name !== "team_snapshot") return null;
  const chatId = asText(value.chat_id);
  if (!chatId) return null;
  const turnId = asText(value.turn_id) ?? "";

  if (name === "team_snapshot") {
    const run = parseRunSnapshot(value.data);
    return run ? { kind: "snapshot", chatId, turnId: run.turnId || turnId, run } : null;
  }
  const event = parseTeamEvent(value.data);
  return event ? { kind: "event", chatId, turnId: event.turnId || turnId, event } : null;
}
