// 团队管理页（Task 8）：CRUD、乐观并发冲突、引用校验与空态。
//
// 关键口径：**客户端就挡掉**没有 Leader / 成员越权的团队（连请求都不发），
// 冲突（TEAM_REVISION_CONFLICT）时保留草稿并提示重新加载，归档必须走确认框。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TeamsView } from "@/components/settings/teams/TeamsView";
import type { AgentProfile } from "@/lib/agents/types";
import type { TeamDefinition } from "@/lib/teams/types";

const { getToken, client, listTeams, listAgents, saveTeam, archiveTeam, cloneTeam } = vi.hoisted(() => ({
  getToken: vi.fn(),
  client: { requestMutation: vi.fn() },
  listTeams: vi.fn(),
  listAgents: vi.fn(),
  saveTeam: vi.fn(),
  archiveTeam: vi.fn(),
  cloneTeam: vi.fn(),
}));

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({
    client,
    token: "tok-1",
    // getToken 必须是稳定引用：TeamsView 的 reload 依赖它，换引用会无限重取。
    getToken,
    modelName: "claude-sonnet-5",
    ingressLimits: null,
  }),
}));

vi.mock("@/lib/teams/api", () => ({
  listTeams: (...args: unknown[]) => listTeams(...args),
  loadTeamDetail: vi.fn(),
  saveTeam: (...args: unknown[]) => saveTeam(...args),
  archiveTeam: (...args: unknown[]) => archiveTeam(...args),
  cloneTeam: (...args: unknown[]) => cloneTeam(...args),
  teamErrorCode: (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error ?? "");
    return message.match(/(TEAM_[A-Z_]+)/)?.[1] ?? null;
  },
}));

vi.mock("@/lib/agents/api", () => ({
  listAgents: (...args: unknown[]) => listAgents(...args),
}));

function agent(id: string, name: string, overrides: Partial<AgentProfile> = {}): AgentProfile {
  return {
    id,
    name,
    description: `${name} 的专长`,
    type: "custom",
    customized: false,
    categoryId: "general",
    icon: "🤖",
    color: "#4A90D9",
    prompt: "",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

const leader = agent("leader", "主持");
const researcher = agent("researcher", "调研");
const writer = agent("writer", "写作");

function team(overrides: Partial<TeamDefinition> = {}): TeamDefinition {
  return {
    id: "content-team",
    revision: 2,
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
    updatedAt: "2026-10-05T00:00:00Z",
    ...overrides,
  };
}

async function startNewTeam(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "New team" }));
  await user.type(await screen.findByLabelText("Team name"), "增长团队");
}

describe("teams 管理页", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getToken.mockResolvedValue("tok-1");
    listTeams.mockResolvedValue({ teams: [team()], nextCursor: null });
    listAgents.mockResolvedValue({ agents: [leader, researcher, writer] });
    saveTeam.mockImplementation(async (_client: unknown, payload: { team: TeamDefinition }) => ({
      team: payload.team,
    }));
    archiveTeam.mockResolvedValue({ id: "content-team", archived: true, revision: 3 });
    cloneTeam.mockResolvedValue({ team: team({ id: "content-team-copy", name: "内容团队 Copy" }) });
  });

  it("列出团队、Leader 与并行上限", async () => {
    render(<TeamsView />);

    await screen.findByText("内容团队");
    expect(screen.getByText("Leader: 主持")).toBeInTheDocument();
    expect(screen.getByText("Up to 3 in parallel")).toBeInTheDocument();
    expect(screen.getByText("调研 / 写作")).toBeInTheDocument();
  });

  it("没有任何团队时给团队自己的空态，而不是 Agent 域的文案", async () => {
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });

    render(<TeamsView />);

    expect(
      await screen.findByText("Create an Agent profile first, then build a team from it."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No agents")).not.toBeInTheDocument();
  });

  it("拒绝没有 Leader 的团队 —— 连请求都不发", async () => {
    const user = userEvent.setup();
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });
    render(<TeamsView />);

    await startNewTeam(user);

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(await screen.findByText("Pick a Leader")).toBeInTheDocument();
    expect(saveTeam).not.toHaveBeenCalled();
  });

  it("成员越出 Leader 档案的委派范围时拦住保存", async () => {
    const user = userEvent.setup();
    listAgents.mockResolvedValue({
      agents: [
        agent("leader", "主持", { subAgents: { mode: "exclude", entries: ["writer"] } }),
        researcher,
        writer,
      ],
    });
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });
    render(<TeamsView />);

    await startNewTeam(user);
    await user.selectOptions(screen.getByLabelText("Leader"), "leader");
    await user.click(screen.getByLabelText("Add 写作 as a member"));

    expect(
      await screen.findByText("The Leader profile does not allow delegating to: 写作"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(saveTeam).not.toHaveBeenCalled();
  });

  it("创建成功后带幂等 requestId 与 expectedRevision=null", async () => {
    const user = userEvent.setup();
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });
    render(<TeamsView />);

    await startNewTeam(user);
    await user.selectOptions(await screen.findByLabelText("Leader"), "leader");
    await user.click(screen.getByLabelText("Add 调研 as a member"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveTeam).toHaveBeenCalledTimes(1));
    const payload = saveTeam.mock.calls[0][1];
    expect(payload.expectedRevision).toBeNull();
    expect(typeof payload.requestId).toBe("string");
    expect(payload.requestId.length).toBeGreaterThan(0);
    expect(payload.team.leaderAgentId).toBe("leader");
    expect(payload.team.members).toEqual([
      { memberId: "m-researcher", agentId: "researcher", role: "调研 的专长" },
    ]);
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("修订冲突时保留草稿并提示重新加载", async () => {
    const user = userEvent.setup();
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });
    saveTeam.mockRejectedValueOnce(
      new Error("TEAM_REVISION_CONFLICT: team content-team is now at v4"),
    );
    render(<TeamsView />);

    await startNewTeam(user);
    await user.selectOptions(await screen.findByLabelText("Leader"), "leader");
    await user.click(screen.getByLabelText("Add 调研 as a member"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(
        "This team was changed elsewhere. Reload before saving — your draft is kept.",
      ),
    ).toBeInTheDocument();
    // 草稿没被清空：名字仍在输入框里，用户不必重打一遍。
    expect(screen.getByLabelText("Team name")).toHaveValue("增长团队");
  });

  it("保存后继续编辑不被基线回填覆盖，下一次保存以新修订为锁基线", async () => {
    const user = userEvent.setup();
    listTeams.mockResolvedValue({ teams: [], nextCursor: null });
    render(<TeamsView />);

    await startNewTeam(user);
    await user.selectOptions(await screen.findByLabelText("Leader"), "leader");
    await user.click(screen.getByLabelText("Add 调研 as a member"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveTeam).toHaveBeenCalledTimes(1));

    // 保存成功后列表把刚落库的定义回填给编辑器；此时继续敲字不能被清掉。
    const nameInput = screen.getByLabelText("Team name");
    await user.type(nameInput, " v2");
    expect(nameInput).toHaveValue("增长团队 v2");

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveTeam).toHaveBeenCalledTimes(2));
    expect(saveTeam.mock.calls[1][1].expectedRevision).toBe(1);
    expect(saveTeam.mock.calls[1][1].team.name).toBe("增长团队 v2");
  });

  it("编辑器只改并行上限，其余限额原样带回不被重置成默认值", async () => {
    const user = userEvent.setup();
    listTeams.mockResolvedValue({
      teams: [
        team({
          limits: {
            maxConcurrency: 2,
            maxTasks: 32,
            maxDispatchRounds: 6,
            taskTimeoutSeconds: 900,
            runTimeoutSeconds: 3600,
          },
        }),
      ],
      nextCursor: null,
    });
    render(<TeamsView />);

    await screen.findByText("内容团队");
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveTeam).toHaveBeenCalledTimes(1));
    expect(saveTeam.mock.calls[0][1].team.limits).toEqual({
      maxConcurrency: 2,
      maxTasks: 32,
      maxDispatchRounds: 6,
      taskTimeoutSeconds: 900,
      runTimeoutSeconds: 3600,
    });
  });

  it("归档必须先确认，再带 expectedRevision 落库", async () => {
    const user = userEvent.setup();
    render(<TeamsView />);

    await screen.findByText("内容团队");
    await user.click(screen.getByRole("button", { name: "Archive" }));

    // 确认框必须讲清「历史运行仍可查看」，这是归档语义的硬要求。
    expect(await screen.findByText(/past runs stay readable/i)).toBeInTheDocument();
    expect(archiveTeam).not.toHaveBeenCalled();

    // 确认框里的 Archive 动作走 portal，DOM 顺序在最后；卡片上那个同名按钮还在。
    const archiveButtons = screen.getAllByRole("button", { name: "Archive" });
    await user.click(archiveButtons[archiveButtons.length - 1]);

    await waitFor(() => expect(archiveTeam).toHaveBeenCalledTimes(1));
    expect(archiveTeam.mock.calls[0]).toEqual([
      expect.anything(),
      "content-team",
      2,
      expect.any(String),
    ]);
    await waitFor(() => expect(screen.getByText("Archived")).toBeInTheDocument());
  });

  it("复制走服务端 clone，不在前端拼新 ID", async () => {
    const user = userEvent.setup();
    render(<TeamsView />);

    await screen.findByText("内容团队");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    await waitFor(() => expect(cloneTeam).toHaveBeenCalledTimes(1));
    expect(cloneTeam.mock.calls[0][1]).toBe("content-team");
    expect(cloneTeam.mock.calls[0][2]).toBe("内容团队 Copy");
    await waitFor(() => expect(screen.getByText("内容团队 Copy")).toBeInTheDocument());
  });

  it("读取失败显示错误而不是假装空态", async () => {
    listTeams.mockRejectedValueOnce(new Error("TEAM_MEMBER_UNAVAILABLE: gateway down"));

    render(<TeamsView />);

    expect(await screen.findByRole("alert")).toHaveTextContent("TEAM_MEMBER_UNAVAILABLE");
    expect(
      screen.queryByText("Create an Agent profile first, then build a team from it."),
    ).not.toBeInTheDocument();
  });

  it("编辑已有团队时带上 expectedRevision 作为乐观锁基线", async () => {
    const user = userEvent.setup();
    render(<TeamsView />);

    await screen.findByText("内容团队");
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveTeam).toHaveBeenCalledTimes(1));
    const payload = saveTeam.mock.calls[0][1];
    expect(payload.expectedRevision).toBe(2);
    expect(payload.team.id).toBe("content-team");
  });
});
