// Agent Team 的读写通道。
//
// 沿用项目既有口径（契约 §3）：**读取走认证 HTTP**，**写入走认证 WebSocket 的
// requestMutation 白名单**，不新增免认证写 HTTP。失败仍是外层 `{ error: string }`，
// 固定错误码前缀（TEAM_REVISION_CONFLICT / TEAM_MEMBER_UNAVAILABLE …）由调用方按
// 前缀判定，不做整站 error envelope 改造。
import { mutation, request, type WebUIMutationTransport } from "@/lib/api";
import type {
  TeamDefinition,
  TeamLimits,
  TeamMember,
  TeamRevisionSnapshot,
  TeamRunSnapshot,
} from "@/lib/teams/types";
import type { TeamEvent } from "@/lib/teams/events";

const TEAMS_BASE = "/api/settings/teams";
const TEAMS_READ_TIMEOUT_MS = 20_000;
const TEAMS_MUTATION_TIMEOUT_MS = 20_000;

export interface TeamListResponse {
  teams: TeamDefinition[];
  nextCursor: string | null;
}

export interface TeamDetailResponse {
  team: TeamDefinition;
  snapshot: TeamRevisionSnapshot;
}

export interface SaveTeamRequest {
  requestId: string;
  team: TeamDefinition;
  /** 创建传 null；更新传已加载到的 revision，服务端不匹配即冲突。 */
  expectedRevision: number | null;
}

/**
 * 创建路径的线格式：契约 §1 把 `revision`/`createdAt`/`updatedAt` 划为服务端盖章字段，
 * Python 侧 `TeamDefinition.from_create_payload` 见到它们直接报错（不是静默丢弃），
 * 所以创建时必须在客户端就不把它们发出去。更新路径原样发送（服务端会自己忽略）。
 */
export interface TeamCreatePayload {
  id: string;
  name: string;
  description: string;
  instructions: string;
  leaderAgentId: string;
  members: TeamMember[];
  limits: TeamLimits;
  archived: boolean;
}

export function toCreatePayload(team: TeamDefinition): TeamCreatePayload {
  return {
    id: team.id,
    name: team.name,
    description: team.description,
    instructions: team.instructions,
    leaderAgentId: team.leaderAgentId,
    members: team.members.map((member) => ({ ...member })),
    limits: { ...team.limits },
    archived: team.archived,
  };
}

export interface TeamMutationResponse {
  team: TeamDefinition;
}

export interface ArchiveTeamResponse {
  id: string;
  archived: true;
  revision: number;
}

export async function listTeams(
  token: string,
  options: { base?: string; cursor?: string | null; limit?: number } = {},
): Promise<TeamListResponse> {
  const params = new URLSearchParams();
  if (options.cursor) params.set("cursor", options.cursor);
  params.set("limit", String(options.limit ?? 20));
  return request<TeamListResponse>(
    `${options.base ?? ""}${TEAMS_BASE}?${params.toString()}`,
    token,
    undefined,
    TEAMS_READ_TIMEOUT_MS,
  );
}

export async function loadTeamDetail(
  token: string,
  id: string,
  options: { base?: string; revision?: number | null } = {},
): Promise<TeamDetailResponse> {
  const params = new URLSearchParams({ id });
  if (options.revision != null) params.set("revision", String(options.revision));
  return request<TeamDetailResponse>(
    `${options.base ?? ""}${TEAMS_BASE}/detail?${params.toString()}`,
    token,
    undefined,
    TEAMS_READ_TIMEOUT_MS,
  );
}

export async function saveTeam(
  transport: WebUIMutationTransport,
  payload: SaveTeamRequest,
): Promise<TeamMutationResponse> {
  const team =
    payload.expectedRevision === null
      ? toCreatePayload(payload.team)
      : payload.team;
  return mutation<TeamMutationResponse>(
    transport,
    "teams.save",
    {
      requestId: payload.requestId,
      team,
      expectedRevision: payload.expectedRevision,
    },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}

export async function archiveTeam(
  transport: WebUIMutationTransport,
  id: string,
  expectedRevision: number,
  requestId: string,
): Promise<ArchiveTeamResponse> {
  return mutation<ArchiveTeamResponse>(
    transport,
    "teams.archive",
    { requestId, id, expectedRevision },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}

export async function cloneTeam(
  transport: WebUIMutationTransport,
  id: string,
  name: string,
  requestId: string,
): Promise<TeamMutationResponse> {
  return mutation<TeamMutationResponse>(
    transport,
    "teams.clone",
    { requestId, id, name },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}

/** 冲突与引用错误靠固定前缀识别：HTTP 层只保证 `error` 是字符串。 */
export function teamErrorCode(error: unknown): string | null {
  const message =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : undefined;
  if (!message) return null;
  const match = message.match(
    /(TEAM_[A-Z_]+|RUN_NOT_FOUND|TASK_NOT_FOUND|CROSS_SESSION_FORBIDDEN|DISPATCH_KEY_CONFLICT|MODEL_PRESET_UNAVAILABLE|AGENT_IN_USE_BY_TEAM)/,
  );
  return match ? match[1] : null;
}

// ------------------------------------------------------------ 运行查询与控制（契约 §5）

export interface TeamRunListResponse {
  runs: TeamRunSnapshot[];
  nextCursor: string | null;
}

export interface TeamRunDetailResponse {
  run: TeamRunSnapshot;
}

export interface TeamRunEventsResponse {
  events: TeamEvent[];
  lastSeq: number;
  hasMore: boolean;
  /** 增量被剪过或客户端自报丢失：必须先回快照，不能继续顺序应用。 */
  resetRequired: boolean;
}

export interface CancelTeamRunResponse {
  runId: string;
  /** 可能仍是 `cancelling`：服务端不提前宣称全部停止。 */
  status: string;
}

export interface CancelTeamTaskResponse {
  taskId: string;
  status: string;
}

export interface RetryTeamRunResponse {
  newRunId: string;
  turnId: string;
  taskIds: string[];
}

export async function listTeamRuns(
  token: string,
  chatId: string,
  options: { base?: string; cursor?: string | null; limit?: number } = {},
): Promise<TeamRunListResponse> {
  const params = new URLSearchParams({ chatId });
  if (options.cursor) params.set("cursor", options.cursor);
  params.set("limit", String(options.limit ?? 20));
  return request<TeamRunListResponse>(
    `${options.base ?? ""}/api/team-runs?${params.toString()}`,
    token,
    undefined,
    TEAMS_READ_TIMEOUT_MS,
  );
}

export async function loadTeamRun(
  token: string,
  runId: string,
  options: { base?: string } = {},
): Promise<TeamRunDetailResponse> {
  const params = new URLSearchParams({ runId });
  return request<TeamRunDetailResponse>(
    `${options.base ?? ""}/api/team-runs/detail?${params.toString()}`,
    token,
    undefined,
    TEAMS_READ_TIMEOUT_MS,
  );
}

export async function loadTeamRunEvents(
  token: string,
  runId: string,
  options: { base?: string; afterSeq?: number; limit?: number } = {},
): Promise<TeamRunEventsResponse> {
  const params = new URLSearchParams({
    runId,
    afterSeq: String(options.afterSeq ?? 0),
    limit: String(options.limit ?? 200),
  });
  return request<TeamRunEventsResponse>(
    `${options.base ?? ""}/api/team-runs/events?${params.toString()}`,
    token,
    undefined,
    TEAMS_READ_TIMEOUT_MS,
  );
}

export async function cancelTeamRun(
  transport: WebUIMutationTransport,
  runId: string,
  requestId: string,
): Promise<CancelTeamRunResponse> {
  return mutation<CancelTeamRunResponse>(
    transport,
    "teamRuns.cancel",
    { requestId, runId },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}

export async function cancelTeamTask(
  transport: WebUIMutationTransport,
  runId: string,
  taskId: string,
  requestId: string,
): Promise<CancelTeamTaskResponse> {
  return mutation<CancelTeamTaskResponse>(
    transport,
    "teamTasks.cancel",
    { requestId, runId, taskId },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}

export async function retryTeamRun(
  transport: WebUIMutationTransport,
  runId: string,
  requestId: string,
): Promise<RetryTeamRunResponse> {
  return mutation<RetryTeamRunResponse>(
    transport,
    "teamRuns.retry",
    { requestId, runId },
    TEAMS_MUTATION_TIMEOUT_MS,
  );
}
