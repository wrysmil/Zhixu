// Agent Team 客户端读写通道（契约 §3：读走认证 HTTP，写走认证 WS 的 requestMutation）。
//
// 这里锁住三件事：① 创建载荷绝不带服务端盖章字段（Python `from_create_payload` 会拒绝）；
// ② 幂等 requestId 与 expectedRevision 必须原样送到 mutation；③ 冲突靠错误码前缀识别。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  archiveTeam,
  cloneTeam,
  listTeams,
  loadTeamDetail,
  saveTeam,
  teamErrorCode,
  toCreatePayload,
} from "@/lib/teams/api";
import type { TeamDefinition } from "@/lib/teams/types";

const requestMutation = vi.fn();
const transport = {
  requestMutation: <T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ) => requestMutation(action, payload, timeoutMs) as Promise<T>,
};

const team: TeamDefinition = {
  id: "content-team",
  revision: 3,
  name: "内容团队",
  description: "选题到成稿",
  instructions: "先调研再写作",
  leaderAgentId: "leader",
  members: [
    { memberId: "m-researcher", agentId: "researcher", role: "资料收集" },
    { memberId: "m-writer", agentId: "writer", role: "成稿" },
  ],
  limits: {
    maxConcurrency: 3,
    maxTasks: 16,
    maxDispatchRounds: 4,
    taskTimeoutSeconds: 600,
    runTimeoutSeconds: 1800,
  },
  archived: false,
  createdAt: "2026-10-01T00:00:00Z",
  updatedAt: "2026-10-10T00:00:00Z",
};

function json(payload: unknown) {
  return {
    ok: true,
    headers: { get: () => "application/json" },
    json: async () => payload,
  };
}

describe("teams api", () => {
  beforeEach(() => {
    requestMutation.mockReset();
    requestMutation.mockResolvedValue({ team });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ teams: [team], nextCursor: null })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("读取带 Bearer 与 limit，缺 cursor 时不拼空参数", async () => {
    await listTeams("tok-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/teams?limit=20",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok-1" },
        credentials: "same-origin",
      }),
    );
  });

  it("详情按 revision 取指定修订", async () => {
    await loadTeamDetail("tok-1", "content-team", { revision: 2 });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/teams/detail?id=content-team&revision=2",
      expect.anything(),
    );
  });

  it("创建时不发送服务端盖章字段", async () => {
    await saveTeam(transport, { requestId: "req-1", team, expectedRevision: null });

    const [, payload] = requestMutation.mock.calls[0];
    expect(payload).toMatchObject({ requestId: "req-1", expectedRevision: null });
    const sent = payload.team as Record<string, unknown>;
    expect(sent).not.toHaveProperty("revision");
    expect(sent).not.toHaveProperty("createdAt");
    expect(sent).not.toHaveProperty("updatedAt");
    expect(sent).toMatchObject({ id: "content-team", leaderAgentId: "leader" });
  });

  it("更新时带上 expectedRevision 做乐观并发", async () => {
    await saveTeam(transport, { requestId: "req-2", team, expectedRevision: 3 });

    const [, payload] = requestMutation.mock.calls[0];
    expect(payload).toMatchObject({ requestId: "req-2", expectedRevision: 3 });
    expect((payload.team as TeamDefinition).revision).toBe(3);
  });

  it("写入统一走 requestMutation 的 teams.* 动作名", async () => {
    await archiveTeam(transport, "content-team", 3, "req-3");
    await cloneTeam(transport, "content-team", "内容团队 副本", "req-4");

    expect(requestMutation.mock.calls[0][0]).toBe("teams.archive");
    expect(requestMutation.mock.calls[0][1]).toMatchObject({
      requestId: "req-3",
      id: "content-team",
      expectedRevision: 3,
    });
    expect(requestMutation.mock.calls[1][0]).toBe("teams.clone");
    expect(requestMutation.mock.calls[1][1]).toMatchObject({ name: "内容团队 副本" });
  });

  it("把 mutation 失败包成带错误码前缀的 ApiError", async () => {
    requestMutation.mockRejectedValueOnce(
      new Error("TEAM_REVISION_CONFLICT: team content-team is now at v4"),
    );

    await expect(
      saveTeam(transport, { requestId: "req-5", team, expectedRevision: 1 }),
    ).rejects.toThrow(/TEAM_REVISION_CONFLICT/);
  });

  it("toCreatePayload 深拷成员与限额，避免改写 store 里的对象", () => {
    const payload = toCreatePayload(team);
    payload.members.push({ memberId: "m-x", agentId: "x", role: "临时" });
    payload.limits.maxConcurrency = 8;

    expect(team.members).toHaveLength(2);
    expect(team.limits.maxConcurrency).toBe(3);
  });

  it("错误码只按固定前缀判定，其他文案不误判", () => {
    expect(
      teamErrorCode(new Error("TEAM_MEMBER_UNAVAILABLE: researcher 已隐藏")),
    ).toBe("TEAM_MEMBER_UNAVAILABLE");
    expect(teamErrorCode("AGENT_IN_USE_BY_TEAM: writer")).toBe("AGENT_IN_USE_BY_TEAM");
    expect(teamErrorCode(new Error("gateway 连接中断"))).toBeNull();
    expect(teamErrorCode(undefined)).toBeNull();
  });
});
