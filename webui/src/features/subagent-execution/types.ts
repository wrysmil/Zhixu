// 普通子 Agent 执行的公开前端类型。字段与契约 §1/§2 逐字一致。
import type { ConnectionStatus } from "@/lib/types";

export type ExecutionStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ExecutionPhase = "preparing" | "model" | "tools" | "finalizing";

export type ExecutionActivityStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface ExecutionResult {
  summary: string;
  truncated: boolean;
  stopReason: string;
  errorCode: string | null;
  errorMessage: string | null;
}

export interface ExecutionActivity {
  activityId: string;
  kind: "tool" | "progress";
  toolName: string | null;
  status: ExecutionActivityStatus;
  label: string;
  startedAt: string;
  finishedAt: string | null;
}

export interface ExecutionSnapshot {
  invocationId: string;
  chatId: string;
  parentTurnId: string;
  parentToolCallId: string;
  agentId: string | null;
  agentName: string;
  title: string;
  instructionSummary: string;
  mode: "background" | "wait";
  status: ExecutionStatus;
  phase: ExecutionPhase;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  queueDurationMs: number | null;
  durationMs: number | null;
  lastAction: string | null;
  completedToolCount: number;
  lastSeq: number;
  result: ExecutionResult | null;
  /** 列表/详情投影可附活动；事件仍以 activity 字段为准。 */
  activities: ExecutionActivity[];
}

export type ExecutionEventType =
  | "execution.created"
  | "execution.started"
  | "execution.phase_changed"
  | "execution.activity"
  | "execution.finished";

export type ExecutionEventSnapshot = Omit<
  ExecutionSnapshot,
  "instructionSummary" | "result" | "activities"
>;

export interface ExecutionEvent {
  schemaVersion: 1;
  invocationId: string;
  chatId: string;
  parentTurnId: string;
  parentToolCallId: string;
  seq: number;
  occurredAt: string;
  type: ExecutionEventType;
  data: {
    execution: ExecutionEventSnapshot;
    activity?: ExecutionActivity;
  };
}

export type ExecutionFrame =
  | { kind: "event"; chatId: string; turnId: string; event: ExecutionEvent }
  | {
      kind: "snapshot";
      chatId: string;
      turnId: string;
      executions: ExecutionSnapshot[];
      nextCursor: string | null;
      serverNow: string;
    };

export interface ExecutionListResponse {
  executions: ExecutionSnapshot[];
  nextCursor: string | null;
  serverNow: string;
}

export interface ExecutionDetailResponse {
  execution: ExecutionSnapshot;
  serverNow: string;
}

export interface ExecutionEventsResponse {
  events: ExecutionEvent[];
  lastSeq: number;
  hasMore: boolean;
  resetRequired: boolean;
}

/**
 * 单个 invocation 的前端投影。
 *
 * `timelineCursor` 只表示连续事件已应用到哪一条；`snapshotRevision` 是服务端
 * 快照最后序号。二者分开，才能让旧快照更新状态而不假装时间线已经恢复。
 */
export interface SubagentExecutionState {
  snapshot: ExecutionSnapshot;
  activities: ExecutionActivity[];
  activityById: Record<string, ExecutionActivity>;
  timelineCursor: number;
  snapshotRevision: number;
  appliedSeqs: number[];
  pendingEvents: ExecutionEvent[];
  needsReplay: boolean;
  historyLimited: boolean;
  connectionStale: boolean;
  error: string | null;
}

export interface SubagentExecutionFrameClient {
  onChat(chatId: string, handler: (event: unknown) => void): () => void;
  onStatus?(handler: (status: ConnectionStatus) => void): () => void;
}

export interface UseSubagentExecutionsOptions {
  chatId: string | null;
  token: string;
  /** 服务端声明 `subagentExecutionEventsV1` 时才允许订阅、补拉和 list。 */
  capability: boolean;
}

export interface SubagentExecutions {
  /** 按 createdAt 降序、invocationId 稳定排序。 */
  executions: SubagentExecutionState[];
  byInvocationId: Readonly<Record<string, SubagentExecutionState>>;
  activeCount: number;
  syncing: boolean;
  connectionStale: boolean;
  historyLimited: boolean;
  /** 打开详情时调用：先读 detail 边界，再从 0 分页恢复时间线。 */
  loadDetail(invocationId: string): Promise<SubagentExecutionState | null>;
  /** 仅重跑 list；不会触发 spawn、取消或重试。 */
  reload(): void;
}
