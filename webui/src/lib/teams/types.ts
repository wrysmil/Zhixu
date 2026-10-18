// Agent Team 前端的领域类型。
//
// 真相源是 `.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md` §1，
// 与 Python 侧 `nanobot/teams/models.py` 逐字段同名（线格式一律 camelCase）。
// 跨语言锚点由 `tests/fixtures/teams/{definition,snapshot,event}.json` 固定，
// 改这里必须同步改 fixture 与 Python 类型，否则边界测试会先失败。

export type TeamRunStatus =
  | "running"
  | "cancelling"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "blocked"
  | "interrupted";

export type SelectionMode = "all" | "include" | "exclude";

export interface CapabilitySelection {
  mode: SelectionMode;
  entries: string[];
}

export interface TeamMember {
  /** 同一团队内稳定，不同于 Agent 档案 ID。 */
  memberId: string;
  agentId: string;
  /** 本团队职责，最多 120 字。 */
  role: string;
}

export interface TeamLimits {
  maxConcurrency: number;
  maxTasks: number;
  maxDispatchRounds: number;
  taskTimeoutSeconds: number;
  runTimeoutSeconds: number;
}

export interface TeamDefinition {
  id: string;
  /** 服务端递增，从 1 开始；客户端不得伪造。 */
  revision: number;
  name: string;
  description: string;
  instructions: string;
  leaderAgentId: string;
  members: TeamMember[];
  limits: TeamLimits;
  archived: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TeamBinding {
  teamId: string;
  teamRevision: number;
  teamName: string;
}

/** What the chat creator needs from a team: enough to label it, pin its revision, and offer its agents. */
export interface TeamOption {
  id: string;
  name: string;
  revision: number;
  /** 二级 Agent 选择的默认落点：该团队的 Leader 档案 id。 */
  leaderAgentId: string;
  /** 二级 Agent 选择的其余候选项：团队成员档案 id，已去重且不含 Leader。 */
  memberAgentIds: string[];
}

export interface AgentProfileSnapshot {
  id: string;
  updatedAt: string;
  prompt: string;
  /** 已配置的模型 preset 名，不是裸模型 ID。 */
  modelId: string | null;
  tools: CapabilitySelection;
  skills: CapabilitySelection;
  subAgents: CapabilitySelection;
  name: string;
}

export interface TeamRevisionMember {
  member: TeamMember;
  profile: AgentProfileSnapshot;
}

export interface TeamRevisionSnapshot {
  definition: TeamDefinition;
  leader: AgentProfileSnapshot;
  members: TeamRevisionMember[];
}

export interface TeamTaskResult {
  taskId: string;
  attemptId: string;
  memberId: string;
  agentId: string;
  status: TaskStatus;
  stopReason: string;
  summary: string;
  artifacts: string[];
  errorCode: string | null;
  usage: { inputTokens: number; outputTokens: number } | null;
}

export interface TeamTaskSnapshot {
  taskId: string;
  memberId: string;
  agentId: string;
  title: string;
  acceptance: string;
  /** 持久化后的 taskId，不再是批次短 key。 */
  dependsOn: string[];
  status: TaskStatus;
  attemptId: string | null;
  /** 经脱敏的公开活动摘要，最多 240 字。 */
  lastAction: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  result: TeamTaskResult | null;
}

export type LeaderAction = "planning" | "waiting" | "reviewing" | "summarizing" | "idle";

export interface TeamRunSnapshot {
  runId: string;
  chatId: string;
  turnId: string;
  binding: TeamBinding;
  status: TeamRunStatus;
  lastSeq: number;
  tasks: TeamTaskSnapshot[];
  leaderAction: LeaderAction;
  startedAt: string;
  finishedAt: string | null;
  parentRunId: string | null;
}

export const TEAM_SCHEMA_VERSION = 1;

export const TEAM_LIMIT_RANGES = {
  maxConcurrency: { min: 1, max: 8, default: 3 },
  maxTasks: { min: 1, max: 64, default: 16 },
  maxDispatchRounds: { min: 1, max: 8, default: 4 },
  taskTimeoutSeconds: { min: 30, max: 1800, default: 600 },
  runTimeoutSeconds: { min: 60, max: 7200, default: 1800 },
} as const;

export const TEAM_MEMBER_MIN = 1;
export const TEAM_MEMBER_MAX = 8;
export const TEAM_NAME_MAX_LENGTH = 80;
export const TEAM_INSTRUCTIONS_MAX_LENGTH = 8000;
export const TEAM_MEMBER_ROLE_MAX_LENGTH = 120;

export function defaultTeamLimits(): TeamLimits {
  return {
    maxConcurrency: TEAM_LIMIT_RANGES.maxConcurrency.default,
    maxTasks: TEAM_LIMIT_RANGES.maxTasks.default,
    maxDispatchRounds: TEAM_LIMIT_RANGES.maxDispatchRounds.default,
    taskTimeoutSeconds: TEAM_LIMIT_RANGES.taskTimeoutSeconds.default,
    runTimeoutSeconds: TEAM_LIMIT_RANGES.runTimeoutSeconds.default,
  };
}
