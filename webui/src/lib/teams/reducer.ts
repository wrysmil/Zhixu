// 团队运行的客户端状态机（契约 §6 的恢复协议）。
//
// 三条硬规则，来自协议本身而不是实现偏好：
//   1. 只按 seq 应用。`seq <= lastSeq` 是重放或重复，必须原样忽略。
//   2. `seq !== lastSeq + 1` 说明中间缺了事件（剪枝或丢帧）：**不猜**，标记
//      `needsReplay` 让上层去补拉。
//   3. 终态不可回退。迟到的 started/activity 不能把已完成的运行或任务改回去。
//
// 状态按 runId / taskId 建索引；attemptId 只是任务的一次执行，卡片永远按 taskId 归并，
// 所以同一个成员的重试不会在面板里裂成两张卡。

import {
  isTerminalRunStatus,
  isTerminalTaskStatus,
  type TeamEvent,
  type TeamEventWithRun,
  type TeamLeaderEvent,
  type TeamTaskEvent,
} from "@/lib/teams/events";
import type {
  LeaderAction,
  TeamRunSnapshot,
  TeamRunStatus,
  TeamTaskSnapshot,
} from "@/lib/teams/types";

export interface TeamRunState {
  runId: string;
  chatId: string;
  turnId: string;
  teamId: string;
  teamRevision: number;
  teamName: string;
  status: TeamRunStatus;
  leaderAction: LeaderAction;
  lastSeq: number;
  startedAt: string;
  taskOrder: string[];
  tasks: Record<string, TeamTaskSnapshot>;
  /** 有事件被跳过或增量被剪掉：视图必须先回快照再继续。 */
  needsReplay: boolean;
  /** 上一次补拉失败的提示，null 表示没有。 */
  error: string | null;
}

export function teamRunStateFromSnapshot(run: TeamRunSnapshot): TeamRunState {
  const tasks: Record<string, TeamTaskSnapshot> = {};
  const taskOrder: string[] = [];
  for (const task of run.tasks) {
    if (!(task.taskId in tasks)) taskOrder.push(task.taskId);
    tasks[task.taskId] = task;
  }
  return {
    runId: run.runId,
    chatId: run.chatId,
    turnId: run.turnId,
    teamId: run.binding.teamId,
    teamRevision: run.binding.teamRevision,
    teamName: run.binding.teamName,
    status: run.status,
    leaderAction: run.leaderAction,
    lastSeq: run.lastSeq,
    startedAt: run.startedAt,
    taskOrder,
    tasks,
    needsReplay: false,
    error: null,
  };
}

export function emptyTeamRunState(runId: string, chatId: string, turnId: string): TeamRunState {
  return {
    runId,
    chatId,
    turnId,
    teamId: "",
    teamRevision: 0,
    teamName: "",
    status: "running",
    leaderAction: "idle",
    lastSeq: 0,
    startedAt: "",
    taskOrder: [],
    tasks: {},
    needsReplay: false,
    error: null,
  };
}

function withTask(state: TeamRunState, task: TeamTaskSnapshot): TeamRunState {
  const known = state.taskOrder.includes(task.taskId);
  const tasks = { ...state.tasks, [task.taskId]: task };
  return {
    ...state,
    tasks,
    taskOrder: known ? state.taskOrder : [...state.taskOrder, task.taskId],
  };
}

function applyRunSnapshot(state: TeamRunState, run: TeamRunSnapshot): TeamRunState {
  const merged = teamRunStateFromSnapshot(run);
  // 快照只会比增量新（lastSeq 单调）；旧快照不许把面板拽回去。
  if (merged.lastSeq < state.lastSeq) return state;
  return {
    ...merged,
    teamName: state.teamName || merged.teamName,
    teamId: state.teamId || merged.teamId,
    error: state.error,
  };
}

function applyTaskEvent(state: TeamRunState, event: TeamTaskEvent): TeamRunState {
  const current = state.tasks[event.task.taskId];
  // 终态任务不被迟到的 started/activity 改写；同 attempt 的重复事件也不动。
  if (current && isTerminalTaskStatus(current.status)) return state;
  if (current && event.task.attemptId && current.attemptId === event.task.attemptId
    && current.status === event.task.status
    && current.lastAction === event.task.lastAction) {
    return state;
  }
  return withTask(state, event.task);
}

export function applyTeamEvent(state: TeamRunState | null, event: TeamEvent): TeamRunState | null {
  // 没有快照时的唯一入口是 run.started：它自带绑定与起点，够把面板立起来。
  // 其余增量只知道 runId，得回服务端要整份（上层负责补拉）。
  // 用 `in` 而不是按 type 收窄：信封上的 type 是整个联合，不是逐成员的字面量。
  if (!state) {
    if (!("binding" in event)) return null;
    return {
      ...emptyTeamRunState(event.runId, event.chatId, event.turnId),
      teamId: event.binding.teamId,
      teamRevision: event.binding.teamRevision,
      teamName: event.binding.teamName,
      lastSeq: event.seq,
      startedAt: event.startedAt,
    };
  }
  // 另一条运行的增量属于别的运行（重试会开出新 run），不能串卡。
  if (event.runId !== state.runId) return state;
  if (event.seq <= state.lastSeq) return state;

  // 带整份运行快照的事件不要求 seq 连续：快照自带全部任务，缺的那几条增量不影响结果。
  // 只有「只带一条变化」的事件才必须严格接续，否则要老实说要补拉。
  const carriesSnapshot = event.type === "run.finished";
  if (!carriesSnapshot && event.seq !== state.lastSeq + 1) {
    return { ...state, needsReplay: true };
  }

  if (isTerminalRunStatus(state.status) && !carriesSnapshot) {
    // 运行已终态：其余事件一律不解释，只把 lastSeq 推上去免得后面的事件全被判成缺口。
    return { ...state, lastSeq: event.seq };
  }

  // 事件接上了就重新算「同步中」：补拉按 seq 逐条回放时走的就是这条路，
  // 不清掉的话面板会一直挂着「Syncing team state…」。
  const synced: TeamRunState = { ...state, needsReplay: false };
  switch (event.type) {
    case "run.started":
      if (!("binding" in event)) return state;
      return {
        ...synced,
        teamId: synced.teamId || event.binding.teamId,
        teamRevision: synced.teamRevision || event.binding.teamRevision,
        teamName: synced.teamName || event.binding.teamName,
        startedAt: synced.startedAt || event.startedAt,
        lastSeq: event.seq,
      };
    case "run.finished":
      return applyRunSnapshot(state, (event as TeamEventWithRun).run);
    case "run.cancelling":
      return { ...synced, status: "cancelling", lastSeq: event.seq };
    case "leader.updated":
      return { ...synced, leaderAction: (event as TeamLeaderEvent).leaderAction, lastSeq: event.seq };
    default:
      return { ...applyTaskEvent(synced, event as TeamTaskEvent), lastSeq: event.seq };
  }
}

export function applyTeamSnapshot(
  state: TeamRunState | null,
  run: TeamRunSnapshot,
): TeamRunState {
  if (!state || state.runId !== run.runId) return teamRunStateFromSnapshot(run);
  return applyRunSnapshot(state, run);
}

export function hasActiveTasks(state: TeamRunState | null): boolean {
  if (!state) return false;
  return state.taskOrder.some((taskId) => {
    const task = state.tasks[taskId];
    return !!task && !isTerminalTaskStatus(task.status);
  });
}

/** 面板用得到的扁平视图：按声明顺序排好，附每个成员名。 */
export interface TeamTaskRow {
  task: TeamTaskSnapshot;
  memberName: string | null;
  agentId: string;
}

export function teamTaskRows(
  state: TeamRunState | null,
  memberNames: Record<string, string> = {},
): TeamTaskRow[] {
  if (!state) return [];
  return state.taskOrder
    .map((taskId) => state.tasks[taskId])
    .filter((task): task is TeamTaskSnapshot => !!task)
    .map((task) => ({
      task,
      memberName: memberNames[task.memberId] ?? null,
      agentId: task.agentId,
    }));
}
