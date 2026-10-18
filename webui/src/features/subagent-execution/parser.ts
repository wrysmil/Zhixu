// 普通子 Agent 执行 wire parser。未知字段、版本和半截载荷一律返回 null。
import type {
  ExecutionActivity,
  ExecutionActivityStatus,
  ExecutionEvent,
  ExecutionEventSnapshot,
  ExecutionEventType,
  ExecutionFrame,
  ExecutionPhase,
  ExecutionResult,
  ExecutionSnapshot,
  ExecutionStatus,
} from "./types";

const STATUSES: readonly ExecutionStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
];

const PHASES: readonly ExecutionPhase[] = ["preparing", "model", "tools", "finalizing"];

const ACTIVITY_STATUSES: readonly ExecutionActivityStatus[] = [
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
];

const EVENT_TYPES: readonly ExecutionEventType[] = [
  "execution.created",
  "execution.started",
  "execution.phase_changed",
  "execution.activity",
  "execution.finished",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asRequiredText(value: unknown): string | null {
  const text = asText(value);
  return text && text.length > 0 ? text : null;
}

function asSequence(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function asNullableSequence(value: unknown): number | null | undefined {
  if (value === null || value === undefined) return null;
  return asSequence(value) ?? undefined;
}

function asStatus<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null;
}

function parseResult(value: unknown): ExecutionResult | null {
  if (!isRecord(value)) return null;
  const summary = asText(value.summary);
  const stopReason = asText(value.stopReason);
  if (summary === null || stopReason === null || typeof value.truncated !== "boolean") return null;
  return {
    summary,
    truncated: value.truncated,
    stopReason,
    errorCode: typeof value.errorCode === "string" ? value.errorCode : null,
    errorMessage: typeof value.errorMessage === "string" ? value.errorMessage : null,
  };
}

export function parseExecutionActivity(value: unknown): ExecutionActivity | null {
  if (!isRecord(value)) return null;
  const activityId = asRequiredText(value.activityId);
  const kind = value.kind === "tool" || value.kind === "progress" ? value.kind : null;
  const status = asStatus(value.status, ACTIVITY_STATUSES);
  const label = asRequiredText(value.label);
  const startedAt = asRequiredText(value.startedAt);
  const toolName = value.toolName === null ? null : asText(value.toolName);
  const finishedAt = value.finishedAt === null ? null : asText(value.finishedAt);
  if (!activityId || !kind || !status || !label || !startedAt || toolName === null && value.toolName !== null) {
    return null;
  }
  if (finishedAt === null && value.finishedAt !== null) return null;
  return { activityId, kind, toolName, status, label, startedAt, finishedAt };
}

function parseSnapshotBase(value: unknown): ExecutionEventSnapshot | null {
  if (!isRecord(value)) return null;
  const invocationId = asRequiredText(value.invocationId);
  const chatId = asRequiredText(value.chatId);
  const parentTurnId = asRequiredText(value.parentTurnId);
  const parentToolCallId = asRequiredText(value.parentToolCallId);
  const agentId = value.agentId === null ? null : asText(value.agentId);
  const agentName = asText(value.agentName);
  const title = asText(value.title);
  const mode = value.mode === "background" || value.mode === "wait" ? value.mode : null;
  const status = asStatus(value.status, STATUSES);
  const phase = asStatus(value.phase, PHASES);
  const createdAt = asRequiredText(value.createdAt);
  const startedAt = value.startedAt === null ? null : asText(value.startedAt);
  const finishedAt = value.finishedAt === null ? null : asText(value.finishedAt);
  const queueDurationMs = asNullableSequence(value.queueDurationMs);
  const durationMs = asNullableSequence(value.durationMs);
  const lastAction = value.lastAction === null ? null : asText(value.lastAction);
  const completedToolCount = asSequence(value.completedToolCount);
  const lastSeq = asSequence(value.lastSeq);
  if (
    !invocationId || !chatId || !parentTurnId || !parentToolCallId
    || agentId === null && value.agentId !== null
    || agentName === null || title === null || !mode || !status || !phase || !createdAt
    || startedAt === null && value.startedAt !== null
    || finishedAt === null && value.finishedAt !== null
    || queueDurationMs === undefined || durationMs === undefined
    || lastAction === null && value.lastAction !== null
    || completedToolCount === null || lastSeq === null
  ) {
    return null;
  }
  return {
    invocationId,
    chatId,
    parentTurnId,
    parentToolCallId,
    agentId,
    agentName,
    title,
    mode,
    status,
    phase,
    createdAt,
    startedAt,
    finishedAt,
    queueDurationMs,
    durationMs,
    lastAction,
    completedToolCount,
    lastSeq,
  };
}

export function parseExecutionSnapshot(value: unknown): ExecutionSnapshot | null {
  const base = parseSnapshotBase(value);
  if (!base || !isRecord(value)) return null;
  if (typeof value.instructionSummary !== "string") return null;
  const result = value.result === null ? null : parseResult(value.result);
  if (value.result !== null && !result) return null;
  const activities = value.activities === undefined
    ? []
    : Array.isArray(value.activities)
      ? value.activities.map(parseExecutionActivity).filter((item): item is ExecutionActivity => item !== null)
      : null;
  if (!activities) return null;
  return { ...base, instructionSummary: value.instructionSummary, result, activities };
}

export function parseExecutionEvent(value: unknown): ExecutionEvent | null {
  if (!isRecord(value) || value.schemaVersion !== 1) return null;
  const invocationId = asRequiredText(value.invocationId);
  const chatId = asRequiredText(value.chatId);
  const parentTurnId = asRequiredText(value.parentTurnId);
  const parentToolCallId = asRequiredText(value.parentToolCallId);
  const seq = asSequence(value.seq);
  const occurredAt = asRequiredText(value.occurredAt);
  const type = asStatus(value.type, EVENT_TYPES);
  if (!invocationId || !chatId || !parentTurnId || !parentToolCallId || seq === null || !occurredAt || !type) {
    return null;
  }
  if (!isRecord(value.data)) return null;
  const execution = parseSnapshotBase(value.data.execution);
  if (!execution) return null;
  const activity = value.data.activity === undefined
    ? undefined
    : parseExecutionActivity(value.data.activity);
  if (value.data.activity !== undefined && !activity) return null;
  if (type === "execution.activity" && !activity) return null;
  return {
    schemaVersion: 1,
    invocationId,
    chatId,
    parentTurnId,
    parentToolCallId,
    seq,
    occurredAt,
    type,
    data: { execution, ...(activity ? { activity } : {}) },
  };
}

export function parseExecutionFrame(value: unknown): ExecutionFrame | null {
  if (!isRecord(value)) return null;
  const name = value.event ?? value.type;
  const chatId = asRequiredText(value.chat_id);
  if (!chatId) return null;
  const turnId = typeof value.turn_id === "string" ? value.turn_id : "";

  if (name === "subagent_execution_event") {
    const event = parseExecutionEvent(value.data);
    return event && event.chatId === chatId
      ? { kind: "event", chatId, turnId: event.parentTurnId || turnId, event }
      : null;
  }
  if (name === "subagent_execution_snapshot") {
    if (!isRecord(value.data) || !Array.isArray(value.data.executions)) return null;
    const executions = value.data.executions
      .map(parseExecutionSnapshot)
      .filter((item): item is ExecutionSnapshot => item !== null);
    if (executions.length !== value.data.executions.length) return null;
    if (executions.some((item) => item.chatId !== chatId)) return null;
    const nextCursor = value.data.nextCursor === null ? null : asText(value.data.nextCursor);
    if (nextCursor === null && value.data.nextCursor !== null) return null;
    return {
      kind: "snapshot",
      chatId,
      turnId: executions[0]?.parentTurnId || turnId,
      executions,
      nextCursor,
      serverNow: typeof value.data.serverNow === "string" ? value.data.serverNow : "",
    };
  }
  return null;
}
