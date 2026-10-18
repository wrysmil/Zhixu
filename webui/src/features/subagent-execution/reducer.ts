// 普通子 Agent 执行的纯投影 reducer。
//
// 这里刻意不按“事件名”覆盖整份快照，而是把每个事件当作对应 seq 的公开状态
// 边界：事件更新 status/phase，快照更新 revision，活动按 activityId 合并。
import type {
  ExecutionActivity,
  ExecutionEvent,
  ExecutionEventSnapshot,
  ExecutionSnapshot,
  ExecutionStatus,
  SubagentExecutionState,
} from "./types";

const TERMINAL_STATUSES: readonly ExecutionStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
];

const TERMINAL_ACTIVITY_STATUSES = new Set<ExecutionActivity["status"]>([
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
]);

export function isTerminalExecutionStatus(status: ExecutionStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

function eventSnapshot(event: ExecutionEvent): ExecutionSnapshot {
  return {
    ...event.data.execution,
    instructionSummary: "",
    result: null,
    activities: [],
  };
}

function mergeSnapshot(
  current: ExecutionSnapshot,
  incoming: ExecutionEventSnapshot | ExecutionSnapshot,
): ExecutionSnapshot {
  const currentTerminal = isTerminalExecutionStatus(current.status);
  // 事件投影（ExecutionEventSnapshot）不携带 result，只有 detail/list 快照才有。
  const detail = "result" in incoming ? (incoming as ExecutionSnapshot) : null;
  if (currentTerminal) {
    // 终态不可被迟到快照或旧事件覆盖（P0-12 / P0-13）：status、phase、durationMs
    // 一律保持先到的终态。唯一例外是事件投影永远拿不到的 instructionSummary /
    // result —— 执行结束后补读详情时必须补上，否则面板永远没有任务说明与公开
    // 结果（真机页面验证发现的 P1）。已经存在的值绝不被覆盖。
    return {
      ...current,
      instructionSummary: current.instructionSummary || detail?.instructionSummary || "",
      result: current.result ?? detail?.result ?? null,
      finishedAt: current.finishedAt ?? detail?.finishedAt ?? null,
      lastSeq: Math.max(current.lastSeq, incoming.lastSeq),
      completedToolCount: Math.max(current.completedToolCount, incoming.completedToolCount),
    };
  }
  return {
    ...current,
    ...incoming,
    lastSeq: Math.max(current.lastSeq, incoming.lastSeq),
    completedToolCount: Math.max(current.completedToolCount, incoming.completedToolCount),
    // 事件投影没有 instructionSummary/result；不能把已有内容抹掉。
    instructionSummary: current.instructionSummary || detail?.instructionSummary || "",
    result: current.result ?? detail?.result ?? null,
    activities: current.activities,
  };
}

function mergeActivityList(
  current: ExecutionActivity[],
  incoming: ExecutionActivity[],
): ExecutionActivity[] {
  const rows = [...current];
  const index = new Map(rows.map((row, position) => [row.activityId, position]));
  for (const activity of incoming) {
    const position = index.get(activity.activityId);
    if (position === undefined) {
      index.set(activity.activityId, rows.length);
      rows.push(activity);
      continue;
    }
    const existing = rows[position];
    if (TERMINAL_ACTIVITY_STATUSES.has(existing.status)) continue;
    rows[position] = { ...existing, ...activity };
  }
  return rows;
}

function withActivities(
  state: SubagentExecutionState,
  activities: ExecutionActivity[],
): SubagentExecutionState {
  const byId: Record<string, ExecutionActivity> = {};
  for (const activity of activities) byId[activity.activityId] = activity;
  return { ...state, activities, activityById: byId };
}

export function executionStateFromSnapshot(snapshot: ExecutionSnapshot): SubagentExecutionState {
  const state: SubagentExecutionState = {
    snapshot,
    activities: [],
    activityById: {},
    timelineCursor: 0,
    snapshotRevision: snapshot.lastSeq,
    appliedSeqs: [],
    pendingEvents: [],
    needsReplay: false,
    historyLimited: false,
    connectionStale: false,
    error: null,
  };
  return withActivities(state, mergeActivityList([], snapshot.activities));
}

export function applyExecutionSnapshot(
  state: SubagentExecutionState | null,
  snapshot: ExecutionSnapshot,
): SubagentExecutionState {
  if (!state || state.snapshot.invocationId !== snapshot.invocationId) {
    return executionStateFromSnapshot(snapshot);
  }
  if (state.snapshot.chatId !== snapshot.chatId) return state;

  const activities = mergeActivityList(state.activities, snapshot.activities);
  if (snapshot.lastSeq < state.snapshotRevision) {
    return withActivities(state, activities);
  }

  const merged = mergeSnapshot(state.snapshot, snapshot);
  const next = withActivities(
    {
      ...state,
      snapshot: merged,
      snapshotRevision: Math.max(state.snapshotRevision, snapshot.lastSeq),
      connectionStale: false,
      error: null,
    },
    activities,
  );
  // 已经启动过时间线时，快照把 revision 推前但不会伪造中间事件。
  const needsReplay = state.timelineCursor > 0
    && next.snapshotRevision > next.timelineCursor;
  return { ...next, needsReplay: state.needsReplay || needsReplay };
}

function applyOneEvent(
  state: SubagentExecutionState,
  event: ExecutionEvent,
): SubagentExecutionState {
  const snapshot = mergeSnapshot(state.snapshot, event.data.execution);
  const activities = event.data.activity
    ? mergeActivityList(state.activities, [event.data.activity])
    : state.activities;
  return withActivities(
    {
      ...state,
      snapshot: {
        ...snapshot,
        lastSeq: Math.max(snapshot.lastSeq, event.seq),
        result: state.snapshot.result,
        instructionSummary: state.snapshot.instructionSummary,
      },
      snapshotRevision: Math.max(state.snapshotRevision, event.seq),
      connectionStale: false,
      error: null,
    },
    activities,
  );
}

function withAppliedEvent(state: SubagentExecutionState, event: ExecutionEvent): SubagentExecutionState {
  const appliedSeqs = state.appliedSeqs.includes(event.seq)
    ? state.appliedSeqs
    : [...state.appliedSeqs, event.seq].sort((left, right) => left - right);
  return {
    ...applyOneEvent(state, event),
    timelineCursor: Math.max(state.timelineCursor, event.seq),
    appliedSeqs,
  };
}

function drainPending(state: SubagentExecutionState): SubagentExecutionState {
  let next = state;
  const pending = [...state.pendingEvents];
  while (true) {
    const position = pending.findIndex((event) => event.seq === next.timelineCursor + 1);
    if (position === -1) break;
    const [event] = pending.splice(position, 1);
    next = withAppliedEvent(next, event);
  }
  return {
    ...next,
    pendingEvents: pending,
    needsReplay: pending.length > 0,
  };
}

export function applyExecutionEvent(
  state: SubagentExecutionState | null,
  event: ExecutionEvent,
): SubagentExecutionState {
  const fresh = state ?? executionStateFromSnapshot(eventSnapshot(event));
  if (fresh.snapshot.invocationId !== event.invocationId || fresh.snapshot.chatId !== event.chatId) {
    return fresh;
  }
  if (state === null) {
    return drainPending(withAppliedEvent(fresh, event));
  }
  if (fresh.appliedSeqs.includes(event.seq) || fresh.pendingEvents.some((item) => item.seq === event.seq)) {
    return fresh;
  }
  if (event.seq <= fresh.timelineCursor) return fresh;

  if (event.seq !== fresh.timelineCursor + 1) {
    // 快照 revision 已经覆盖的旧事件不再进入时间线；否则缓存，等缺号补齐。
    if (event.seq <= fresh.snapshotRevision && fresh.timelineCursor === 0) return fresh;
    return {
      ...fresh,
      pendingEvents: [...fresh.pendingEvents, event].sort((left, right) => left.seq - right.seq),
      needsReplay: true,
    };
  }

  return drainPending(withAppliedEvent(fresh, event));
}

export function applyExecutionEvents(
  state: SubagentExecutionState | null,
  events: ExecutionEvent[],
): SubagentExecutionState | null {
  let next = state;
  for (const event of events) next = applyExecutionEvent(next, event);
  return next;
}

/** 时间线游标推进后，继续消费此前因断号而缓存的事件。 */
export function resumeExecutionTimeline(
  state: SubagentExecutionState,
): SubagentExecutionState {
  return drainPending(state);
}

export function markExecutionConnection(
  state: SubagentExecutionState,
  stale: boolean,
): SubagentExecutionState {
  return { ...state, connectionStale: stale };
}

export function markExecutionHistoryLimited(
  state: SubagentExecutionState,
): SubagentExecutionState {
  return {
    ...state,
    historyLimited: true,
    needsReplay: false,
    pendingEvents: [],
  };
}
